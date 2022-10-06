from typing import *
import time
import threading
from math import inf
from contextvars import ContextVar, Token
from asyncio import CancelledError as AsyncCancelledError
from concurrent.futures import CancelledError as SyncCancelledError

__all__ = [
	'CancelledError',
	'CancelScope',
]


__version__ = '1.0.0'

_current_cancel_scope = ContextVar('_current_cancel_scope', default=None)


class CancelledError(AsyncCancelledError, SyncCancelledError):
	"""Raised to indicate the current operation has been cancelled.
	
	Inherits from asyncio/concurrent.futures CancelledError classes, so all
	cancellation errors can be handled together
	"""


class CancelScope:
	"""Context-aware cancellation scope for handling cancellation and timeout
	easily across every layer, with optional manual controls."""

	def __init__(self, timeout: Optional[float] = None, shield: bool = False, exc=None,
		on_enter: bool = False, on_exit: bool = False) -> None:
		"""Init scope
		
		Args:
			timeout: Optional: Number of seconds to wait before the scope timesout
				Defaults to None which never times out.
			shield: Optional. If True, protect from parent cancellation.
				Defaults to False.
			exc: Optional. Exception instance to raise when the scope is 
				cancelled or times out. Defaults to a `CancelledError` instance
				defined in this package.
			on_enter: Optional. True if you want to automatically check for cancellation
				on entering the scope. Defaults to False.
			on_exit: Optional. True if you want to automatically check for cancellation
				on exiting the scope when no other exception was raised in the scope.
				Defaults to False.
		"""
		self._lock = threading.Lock()
		self._timeout = timeout
		self._shield = shield
		self._exc = exc
		self._entered: Optional[float] = None
		self._deadline: Optional[float] = None
		self._token: Optional[Token] = None
		self._cancelled: bool = False
		self._parent: Optional[CancelScope] = None
		self._children: List[CancelScope] = []
		self._on_enter = on_enter
		self._on_exit = on_exit

	def __enter__(self) -> 'CancelScope':
		if self._entered is not None:
			raise RuntimeError('Cancel scope already entered.')
		self._entered = time.time()
		self._parent: Optional[CancelScope] = _current_cancel_scope.get()
		# timeout cannot change once set, so internally use parent timeout as
		# new one if it is less than that of this scope so it will get applied
		# and we dont have to keep querying the parent
		if self._parent is not None:
			self._parent._add_child(self)
			# copy parent exception if one not defined for this scope, so contextual
			# error message can be raised explaining what operation was cancelled
			if self._exc is None:
				self._exc = self._parent._exc
			if not self._shield:
				parent_timeout = self._parent.timeout()
				if self._timeout is None:
					self._timeout = parent_timeout
				else:
					self._timeout = min(self._timeout, parent_timeout)
		if self._timeout is not None:
			self._deadline = self._entered + self._timeout
		if self._on_enter:
			self.check()
		self._token = _current_cancel_scope.set(self)
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		_current_cancel_scope.reset(self._token)
		self._token = None
		if exc_val is not None and self._on_exit:
			self.check()
	
	def timeout(self) -> float:
		with self._lock:
			if self._cancelled:
				return 0
		if self._timeout is None:
			return inf
		if self._entered is None:
			return self._timeout
		remsecs = self._deadline - time.time()
		return 0 if remsecs <= 0 else remsecs

	def cancelled(self) -> bool:
		"""Return True if cancelled; False otherwise."""
		with self._lock:
			return self._cancelled

	def _add_child(self, child: 'CancelScope') -> None:
		with self._lock:
			if child is self:
				raise ValueError('Cannot add current scope to current scope as a child.')
			# if this parent already cancelled, this new child missed the cancel
			# call, so have to auto-cancel. doing this instead of error in case of 
			# concurrency situation where children keep getting created/added after cancel of parent
			if self._cancelled:
				child._cancel(self)
				return
			self._children.append(child)

	def _cancel(self, cs: 'CancelScope') -> bool:
		with self._lock:
			if self._cancelled:
				return True
			# shield this scope from cancellation if the parent is trying to cancel it
			if cs is self._parent and self._shield:
				return False
			self._cancelled = True
			for i, child in reversed(list(enumerate(self._children))):
				child._cancel(self)
				# dont need to know about the children after they have been cancelled
				del self._children[i]
			return True

	def cancel(self) -> bool:
		"""Cancel the current scope and all its unshielded children and return
		True is successful and False otherwise.
		
		Children are cancelled in the order they were registered.
		
		Returns:
			True if this scope is cancelled already or this call cancels it.
			Zero or more children may be shielded from cancellation, but the return
			value will not be affected by this.
		"""
		return self._cancel(self)

	def check(self) -> None:
		"""Convenience method to check if the scope has been cancelled or 
		has timed out and raise an exception if so; otherwise, return."""
		if self.timeout() > 0:
			return
		if self._exc is None:
			raise CancelledError()
		raise self._exc
