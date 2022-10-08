from typing import *
import time
import inspect
import asyncio
import threading
from contextvars import ContextVar, Token
from concurrent.futures import CancelledError
from asyncio import CancelledError as AsyncCancelledError

__all__ = [
	'CancelScope',
	'AsyncCancelScope',
]


__version__ = '1.0.2'

_current_cancel_scope = ContextVar('_current_cancel_scope', default=None)


def _run_sync(func, *args, **kwargs):
	if inspect.iscoroutinefunction(func):
		return asyncio.run(func(*args, **kwargs))
	else:
		return func(*args, **kwargs)


async def _run_async(func, *args, **kwargs):
	if inspect.iscoroutinefunction(func):
		return await func(*args, **kwargs)
	else:
		return await asyncio.to_thread(func, *args, **kwargs)

class CancelScope:
	"""Synchronous context-aware cancellation scope for handling cancellation and timeout
	easily across every layer, with optional manual controls."""

	def __init__(self, timeout: Optional[float] = None, shield: bool = False, exc=None,
		check_on_enter: bool = False, check_on_exit: bool = False, cancel_on_exc: bool = True,
		bubble: bool = False) -> None:
		"""Init scope
		
		Args:
			timeout: Optional: Number of seconds to wait before the scope timesout
				Defaults to None which never times out.
			shield: Optional. If True, protect from parent cancellation.
				If a scope is shielded and its parent is cancelled, it will not cancel itself
				or its children. A shield on a scope effectively shields all of its descendents.
				If a scope is unshielded and its parent is cancelled, it and its descendents will
				be cancelled as well.
				Defaults to False.
			exc: Optional. Exception instance to raise when the scope is 
				cancelled or times out. Defaults to a CancelledError instance.
			check_on_enter: Optional. If True, call `check()` on entering the scope context.
				Defaults to False.
			check_on_exit: Optional. If True, call `check()` on exiting the scope context,
				so long as no exceptions were raised in the scope context.
				Defaults to False.
			cancel_on_exc: Optional. If True, automatically cancel scope on exception raised
				when used as a context manager. Defaults to True.
			bubble: Optional. If True, anytime a descendent scope of this one is cancelled,
				that cancellation will be bubbled up to all parents, ending at this one, 
				and each of those parents will be cancelled and so will their children.
				This can be useful when you need to halt all operations if any fail. 
				Defaults to False.
		"""
		self._lock = threading.Lock()
		self._timeout = timeout
		self._shield = shield
		self._exc = exc
		self._entered: Optional[float] = None
		self._exited: Optional[float] = None
		self._deadline: Optional[float] = None
		self._token: Optional[Token] = None
		self._cancelled: bool = False
		self._parent: Optional[CancelScope] = None
		self._children: List[CancelScope] = []
		self._check_on_enter = check_on_enter
		self._check_on_exit = check_on_exit
		self._cancel_on_exc = cancel_on_exc
		self._bubble = bubble

	def __enter__(self) -> 'CancelScope':
		if self._entered is not None:
			raise RuntimeError('Cancel scope already entered.')
		self._entered = time.time()
		self._parent: Optional[Union['AsyncCancelScope', 'CancelScope']] = _current_cancel_scope.get()
		# timeout cannot change once set, so internally use parent timeout as
		# new one if it is less than that of this scope so it will get applied
		# and we dont have to keep querying the parent
		if self._parent is not None:
			_run_sync(self._parent._add_child, self)
			# copy parent exception if one not defined for this scope, so contextual
			# error message can be raised explaining what operation was cancelled
			if self._exc is None:
				self._exc = self._parent._exc
			if self._parent._bubble:
				self._bubble = True
			if not self._shield:
				parent_timeout = self._parent.timeout()
				if parent_timeout is not None:
					if self._timeout is None:
						self._timeout = parent_timeout
					else:
						self._timeout = min(self._timeout, parent_timeout)
		if self._timeout is not None:
			self._deadline = self._entered + self._timeout
		if self._check_on_enter:
			self.check()
		self._token = _current_cancel_scope.set(self)
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		_current_cancel_scope.reset(self._token)
		self._token = None
		if exc_val is not None and self._cancel_on_exc:
			self._cancel(self)
		if exc_val is None and self._check_on_exit:
			self.check()
	
	def timeout(self) -> Optional[float]:
		"""Return the number of seconds remaining until this scope times out or 
		None if unlimited time remains.
		
		If already cancelled, 0 is returned.
		If scope context not entered yet, the timeout setting for the scope will
			be returned.
		"""
		if self._cancelled:
			return 0
		if self._timeout is None:
			return None  # None usually means no timeout for most python packages
		if self._entered is None:
			return self._timeout
		remsecs = self._deadline - time.time()
		return 0 if remsecs <= 0 else remsecs

	@property
	def cancelled(self) -> bool:
		"""Return True if cancelled; False otherwise."""
		return self._cancelled

	def _add_child(self, child: Union['AsyncCancelScope', 'CancelScope']) -> None:
		if child is self:
			raise ValueError('Cannot add current scope to current scope as a child.')
		with self._lock:
			# if this parent already cancelled, this new child missed the cancel
			# call, so have to auto-cancel. doing this instead of error in case of 
			# concurrency situation where children keep getting created/added after cancel of parent
			if self._cancelled:
				_run_sync(child._cancel, self)
				return
			self._children.append(child)

	def _cancel(self, cs: Union['AsyncCancelScope', 'CancelScope']) -> bool:
		# shield this scope from cancellation if the parent is trying to cancel it
		# and shield in place
		if cs is self._parent and self._shield:
			return False
		with self._lock:
			if self._cancelled:
				return True
			self._cancelled = True
			while self._children:
				child = self._children.pop()
				# skip cancelling child if it cancelled this scope which is only done
				# when a child bubbles up a cancellation, so the child is already cancelled
				if child is cs: continue
				_run_sync(child._cancel, self)
		# bubble up and out the cancellation signal to the parent and then to
		# 	all its children so everything stops if anything is cancelled
		# 	but only if the parent also has bubble=True, so that the setting only
		# 	gets applied on the scope it is True and the children of that scope but
		# 	not the parent of that scope, unless that also has it set to True.
		# if the parent scope directly cancelled this scope, do not bubble up to it
		# 	to avoid inf loop
		if self._parent and self._parent is not cs and self._bubble and self._parent._bubble:
			_run_sync(self._parent._cancel, self)
		return True

	def cancel(self) -> bool:
		"""Cancel the current scope and all its unshielded children and return
		True is successful and False otherwise.
		
		Order in which children are cancelled is not specified.
		
		Returns:
			True if this scope is cancelled already or this call cancels it.
			Zero or more children may be shielded from cancellation, but the return
			value will not be affected by this.
		"""
		return self._cancel(self)

	def check(self) -> None:
		"""Check if the scope has been cancelled or has timed out and 
		automatically cancel the scope and raise an exception.
		
		The shield will not stop this method from raising an exception.
		The shield only applies in the event of a parent trying to cancel
		its children.
		"""
		timeout = self.timeout()
		if timeout is None:
			return
		if timeout > 0:
			return
		self._cancel(self)
		if self._exc is None:
			raise CancelledError()
		raise self._exc


class AsyncCancelScope(CancelScope):

	def __init__(self, *args, **kwargs) -> None:
		super().__init__(*args, **kwargs)
		self._lock = asyncio.Lock()
	
	__init__.__doc__ = CancelScope.__init__.__doc__

	async def __aenter__(self) -> 'AsyncCancelScope':
		if self._entered is not None:
			raise RuntimeError('Cancel scope already entered.')
		self._entered = time.time()
		self._parent: Optional[Union['AsyncCancelScope', 'CancelScope']] = _current_cancel_scope.get()
		# timeout cannot change once set, so internally use parent timeout as
		# new one if it is less than that of this scope so it will get applied
		# and we dont have to keep querying the parent
		if self._parent is not None:
			await _run_async(self._parent._add_child, self)
			# copy parent exception if one not defined for this scope, so contextual
			# error message can be raised explaining what operation was cancelled
			if self._exc is None:
				self._exc = self._parent._exc
			if self._parent._bubble:
				self._bubble = True
			if not self._shield:
				parent_timeout = self._parent.timeout()
				if parent_timeout is not None:
					if self._timeout is None:
						self._timeout = parent_timeout
					else:
						self._timeout = min(self._timeout, parent_timeout)
		if self._timeout is not None:
			self._deadline = self._entered + self._timeout
		if self._check_on_enter:
			await self.check()
		self._token = _current_cancel_scope.set(self)
		return self

	async def __aexit__(self, exc_type, exc_val, exc_tb):
		_current_cancel_scope.reset(self._token)
		self._token = None
		if exc_val is not None and self._cancel_on_exc:
			await self._cancel(self)
		if exc_val is None and self._check_on_exit:
			await self.check()

	async def _add_child(self, child: Union['AsyncCancelScope', 'CancelScope']) -> None:
		if child is self:
			raise ValueError('Cannot add current scope to current scope as a child.')
		async with self._lock:
			# if this parent already cancelled, this new child missed the cancel
			# call, so have to auto-cancel. doing this instead of error in case of 
			# concurrency situation where children keep getting created/added after cancel of parent
			if self._cancelled:
				await _run_async(child._cancel, self)
				return
			self._children.append(child)

	async def _cancel(self, cs: 'AsyncCancelScope') -> bool:
		# shield this scope from cancellation if the parent is trying to cancel it
		# and shield in place
		if cs is self._parent and self._shield:
			return False
		async with self._lock:
			if self._cancelled:
				return True
			self._cancelled = True
			while self._children:
				child = self._children.pop()
				# skip cancelling child if it cancelled this scope which is only done
				# when a child bubbles up a cancellation, so the child is already cancelled
				if child is cs: continue
				await _run_async(child._cancel, self)
		# bubble up and out the cancellation signal to the parent and then to
		# 	all its children so everything stops if anything is cancelled
		# 	but only if the parent also has bubble=True, so that the setting only
		# 	gets applied on the scope it is True and the children of that scope but
		# 	not the parent of that scope, unless that also has it set to True.
		# if the parent scope directly cancelled this scope, do not bubble up to it
		# 	to avoid inf loop
		if self._parent and self._parent is not cs and self._bubble and self._parent._bubble:
			await _run_async(self._parent._cancel, self)
		return True

	async def cancel(self) -> bool:
		return await self._cancel(self)

	cancel.__doc__ = CancelScope.cancel.__doc__

	async def check(self) -> None:
		timeout = self.timeout()
		if timeout is None:
			return
		if timeout > 0:
			return
		await self._cancel(self)
		if self._exc is None:
			raise AsyncCancelledError()
		raise self._exc
	
	check.__doc__ = CancelScope.check.__doc__


AsyncCancelScope.__doc__ = CancelScope.__doc__
