# cancel_scope
Threadsafe cancellation scope context manager for sync/async code

## Preamble

There are often times when you have nested code and a timeout can be used in multiple calls at any layer. To keep to an overall timeout for the entire operation you might pass along the start time of the operation and then recalculate the remaining timeout to use for other calls. 

This can quickly grow tedious.

This package provides a CancelScope, similar to [trio's](https://trio.readthedocs.io/en/stable/reference-core.html#trio.CancelScope).
Key differences from the trio version:
- works with async **& sync** code
- **cancellation exceptions must be manually raised** by the code using the scope. this avoids exceptions popping up in the awkward places, which often make async cancellation difficult to work with in practice.

Documentation consists of what you see here and the docs in the code.

## Table of Contents


## Inspiration
- [python trio](https://trio.readthedocs.io/en/stable/reference-core.html#trio.CancelScope)
- [golang context](https://pkg.go.dev/context)

## Technologies
- Python >=3.6

## Example 1: Timeout Cancellation at Parent Level
The first example demonstrates how the timeout of a parent affects its children both in the unshielded and shielded cases.


### Code
```python
import time

from cancel_scope import CancelScope, CancelledError


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

### Output
```text
timeout: 3.0
timeout: 0.978079080581665
elapsed: 2.021920919418335
timeout: 0
elapsed: 4.038066625595093
work1 cancelled!
```

## Example 2: Manual Cancellation at Parent Level
This example demonstrates how a manual cancellation from the parent affects the shielded and unshielded children.

### Code
```python
import time

from cancel_scope import CancelScope, CancelledError


def work3():
	with CancelScope(exc=Exception('work3 cancelled!')) as cs:
		time.sleep(1)
		cs.check()
		time.sleep(1)
		cs.check()


def work4():
	with CancelScope(exc=Exception('work4 cancelled!'), shield=True) as cs:
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

### Output
```text
timeout: inf
timeout: 0
elapsed: 2.0253379344940186
timeout: 0
elapsed: 4.046663999557495
work3 cancelled!
```
