# cancel_scope
Async/Sync cancellation scope context manager

## Preamble

There are often times when you have nested code and a timeout can be used in multiple calls at any layer. To keep to an overall timeout for the entire operation you might pass along the start time of the operation and then recalculate the remaining timeout to use for other calls. This can quickly grow tedious.

This package seeks to solve this problem and others related to cancellation with the following features:
- decide exactly when to `check()` the `CancelScope`, so `CancelledError` does not show up in awkward points in the code, making graceful/clean shutdown easier
- default to applying cancellation signals to only the descendants created in the context of the `CancelScope` being cancelled 
- optionally bubble up & out cancellation signals from children up to parents, making it easy to cancel everything in context if any operation fails
- optionally shield a `CancelScope` and its descendants from cancellation by parents, ensuring a critical child operation is not interrupted except by its own cancellation or timeout signals
- works with sync/async code, so it can be used everywhere

Documentation consists of what you see here and the docs in the code.

## Table of Contents
<!-- TOC -->

- [cancel_scope](#cancel_scope)
	- [Preamble](#preamble)
	- [Table of Contents](#table-of-contents)
	- [Inspiration](#inspiration)
	- [Technologies](#technologies)
	- [Warnings](#warnings)
	- [Examples](#examples)
		- [Example 1: Timeout Cancellation at Parent Level](#example-1-timeout-cancellation-at-parent-level)
			- [Code](#code)
			- [Output](#output)
		- [Example 2: Manual Cancellation at Parent Level](#example-2-manual-cancellation-at-parent-level)
			- [Code](#code)
			- [Output](#output)
		- [Example 3: Bubble up & out a cancellation from a child to all descendants of the parent](#example-3-bubble-up--out-a-cancellation-from-a-child-to-all-descendants-of-the-parent)
			- [Code](#code)
			- [Output](#output)
		- [Example 4: Combining CancelScope & AsyncCancelScope](#example-4-combining-cancelscope--asynccancelscope)
			- [Code](#code)
			- [Output](#output)

<!-- /TOC -->

## Inspiration
- [python trio](https://trio.readthedocs.io/en/stable/reference-core.html#trio.CancelScope)
- [golang context](https://pkg.go.dev/context)

## Technologies
- Python >=3.6

## Warnings
- This package uses `contextvars`, so all `contextvars`-aware concurrency libraries can use this package. If a concurrency package is not aware of `contextvars`, then new threads/tasks may create `CancelScope` instances outside the parent `CancelScope` and cancellations wont get applied to those children.
- There is going to be a small performance hit when async/sync are mixed together because async calling sync must push the call to a thread and sync calling async must push off to a running event loop. This seems to be unavoidable. If someone has an alternative, I am all ears.

## Examples
### Example 1: Timeout Cancellation at Parent Level
The first example demonstrates how the timeout of a parent affects its children both in the unshielded and shielded cases.

#### Code
```python
import time

from cancel_scope import CancelScope


def work1():
	with CancelScope(timeout=3, exc=Exception('work1 cancelled!')) as cs:
		time.sleep(1)
		cs.check()
		time.sleep(1)
		cs.check()


def work2():
	with CancelScope(exc=Exception('work2 cancelled!'), shield=True) as cs:
		time.sleep(1)
		cs.check()
		time.sleep(1)
		cs.check()


# example using cancel scopes in child operations with one of them shielded
# and the timeout cancellation getting skipped
try:
	started = time.time()
	with CancelScope(timeout=3) as cs:
		print(f'timeout: {cs.timeout()}')
		work1()
		print(f'timeout: {cs.timeout()}')
		print(f'elapsed: {time.time() - started}')
		work2()
		print(f'timeout: {cs.timeout()}')
		print(f'elapsed: {time.time() - started}')
		work1()
except Exception as exc:
	print(exc)
```

#### Output
```text
timeout: 3.0
timeout: 0.978079080581665
elapsed: 2.021920919418335
timeout: 0
elapsed: 4.038066625595093
work1 cancelled!
```

### Example 2: Manual Cancellation at Parent Level
This example demonstrates how a manual cancellation from the parent affects the shielded and unshielded children.

#### Code
```python
import time
from concurrent.futures import CancelledError

from cancel_scope import CancelScope


def work3():
	with CancelScope(exc=CancelledError('work3 cancelled!')) as cs:
		time.sleep(1)
		cs.check()
		time.sleep(1)
		cs.check()


def work4():
	with CancelScope(exc=CancelledError('work4 cancelled!'), shield=True) as cs:
		time.sleep(1)
		cs.check()
		time.sleep(1)
		cs.check()


# example of parent cancelling child operations manually
try:
	started = time.time()
	with CancelScope(exc=CancelledError('Parent scope cancelled!')) as cs:
		print(f'timeout: {cs.timeout()}')
		work3()
		cs.cancel()
		print(f'timeout: {cs.timeout()}')
		print(f'elapsed: {time.time() - started}')
		work4()
		print(f'timeout: {cs.timeout()}')
		print(f'elapsed: {time.time() - started}')
		work3()
except Exception as exc:
	print(exc)

```

#### Output
```text
timeout: inf
timeout: 0
elapsed: 2.0253379344940186
timeout: 0
elapsed: 4.046663999557495
work3 cancelled!
```

### Example 3: Bubble up & out a cancellation from a child to all descendants of the parent
This demonstrates how a cancellation of one child operation can trigger the cancellation of all descendents under a common parent, making it easier to cancel everything when one thing fails.

#### Code
```python
import asyncio

from cancel_scope import AsyncCancelScope


async def work5():
	async with AsyncCancelScope(timeout=3, exc=asyncio.CancelledError('work5 cancelled!')) as pcs:
		print(f'work5-parent cancelled={pcs.cancelled}')
		async with AsyncCancelScope(timeout=3, exc=asyncio.CancelledError('work5 cancelled!')) as ccs:
			print(f'work5-child cancelled={ccs.cancelled}')
			print(f'cancel work5-child')
			await ccs.cancel()
			print(f'work5-child cancelled={ccs.cancelled}')
		print(f'work5-parent cancelled={pcs.cancelled}')


async def work6():
	async with AsyncCancelScope(exc=asyncio.CancelledError('work6 cancelled!')) as cs:
		print(f'work6 cancelled={cs.cancelled}')
		await asyncio.sleep(0)
		print(f'work6 cancelled={cs.cancelled}')


async def main():
	try:
		async with AsyncCancelScope(bubble=True) as cs:
			tasks = []
			tasks.append(asyncio.create_task(work6()))
			tasks.append(asyncio.create_task(work5()))
			await asyncio.wait(tasks)
			print(f'main cancelled={cs.cancelled}')
	except Exception as exc:
		print(exc)


asyncio.run(main())

```

#### Output
```text
work6 cancelled=False
work5-parent cancelled=False
work5-child cancelled=False 
cancel work5-child
work5-child cancelled=True  
work5-parent cancelled=True 
work6 cancelled=True        
main cancelled=True
```

### Example 4: Combining CancelScope & AsyncCancelScope
the synchronous/thread code is able to bubble up a cancellation to the async code

#### Code
```python
import asyncio
import time

from cancel_scope import AsyncCancelScope, CancelScope


def sync_work1():
	with CancelScope(exc=asyncio.CancelledError('sync_work1 cancelled!')) as cs:
		print('sync_work1 started')
		time.sleep(0.1)
		cs.cancel()
		print('sync_work1 cancelled manually')


def sync_work2():
	with CancelScope(exc=asyncio.CancelledError('sync_work2 cancelled!')) as cs:
		print('sync_work2 started')
		time.sleep(0.3)
		print(f'sync_work2 cancelled={cs.cancelled}')


async def async_work():
	try:
		async with AsyncCancelScope(exc=asyncio.CancelledError('async_work cancelled!'), bubble=True) as cs:
			print('async_work started')
			await asyncio.create_task(asyncio.sleep(0))
			await asyncio.gather(asyncio.to_thread(sync_work1), asyncio.to_thread(sync_work2))
			await cs.check()
	except asyncio.CancelledError as exc:
		print(exc)


asyncio.run(async_work())

```

#### Output
```text
async_work started
sync_work1 started
sync_work2 started
sync_work1 cancelled manually
sync_work2 cancelled=True
async_work cancelled!
```
