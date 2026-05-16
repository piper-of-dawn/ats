# Function Augmentation Decorators in Python

Sometimes a function is already doing the right job, and you do not want to replace it. You just want to give it one more trick.

That is the idea here.

In Python, functions are objects. That means you can attach things to them, pass them around, wrap them, and generally treat them like little bundles of behavior. A decorator that *augments* a function takes advantage of that: it keeps the original function usable, but adds extra behavior around it.

In our case, the extra behavior is:

> "This function can run once on one ticker, but it also knows how to run itself across many tickers in parallel."

That sounds fancy, but it is really just this:

```python
func.parallel = some_other_function
```

The decorator is just the neat, reusable way to attach that extra method.

## The Core Idea

Suppose you have a normal function:

```python
def square(x):
    return x * x
```

This works fine:

```python
square(4)  # 16
```

But because `square` is an object, Python lets you do this too:

```python
square.description = "Compute x squared"
```

Now the function has data attached to it:

```python
print(square.description)
```

That same idea can be used for behavior:

```python
def explain(x):
    return f"The square of {x} is {square(x)}"

square.explain = explain
```

Now:

```python
square(4)          # 16
square.explain(4)  # "The square of 4 is 16"
```

We did not replace `square`. We augmented it.

## Why This Is Useful

Usually, when people first learn decorators, they see examples like logging, timing, caching, or access control.

Those are all good. But they often change what happens *when you call the function itself*.

This pattern is different.

We are saying:

- keep the original call path simple
- attach an extra capability
- make the API feel natural

So instead of:

```python
run_parallel(square, values)
```

you can write:

```python
square.parallel(values)
```

That reads better because the parallel behavior belongs to the function.

## A Tiny Example

Let us build a simple decorator that adds a `.repeat()` method to any function.

```python
from functools import wraps


def with_repeat():
    def decorator(func):
        @wraps(func)
        def repeat(times, *args, **kwargs):
            return [func(*args, **kwargs) for _ in range(times)]

        func.repeat = repeat
        return func

    return decorator
```

Use it like this:

```python
@with_repeat()
def greet(name):
    return f"Hello, {name}"
```

Now both of these work:

```python
greet("Ada")
greet.repeat(3, "Ada")
```

Results:

```python
"Hello, Ada"
["Hello, Ada", "Hello, Ada", "Hello, Ada"]
```

That is the pattern in miniature.

## What Is Actually Happening

Let us slow it down.

When Python sees:

```python
@with_repeat()
def greet(name):
    return f"Hello, {name}"
```

it is roughly doing this:

```python
def greet(name):
    return f"Hello, {name}"

greet = with_repeat()(greet)
```

Then inside the decorator, we attach:

```python
greet.repeat = repeat
```

So the decorated function is still the original callable function, but now it carries an extra callable on the side.

That is why this feels so nice:

- `greet(...)` is still the simple case
- `greet.repeat(...)` is the powered-up case

## The Real Example: Adding `.parallel()`

Now for the practical version.

Suppose we have a function that fetches one thing for one ticker:

```python
def fetch_price(ticker):
    ...
```

That function has one job: take one ticker, return one result.

We do **not** want to turn it into a function that suddenly expects a whole list of tickers. That makes the basic function less clear.

Instead, we can attach a `.parallel()` method to it.

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps


def with_parallel_runner(result_name="value", max_workers=20):
    def decorator(func):
        @wraps(func)
        def parallel(items, *args, max_workers=max_workers, **kwargs):
            results = []
            errors = []

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_item = {
                    executor.submit(func, item, *args, **kwargs): item
                    for item in items
                }

                for future in as_completed(future_to_item):
                    item = future_to_item[future]
                    try:
                        value = future.result()
                        results.append({"ticker": item, result_name: value})
                    except Exception as exc:
                        errors.append({"ticker": item, "error": str(exc)})

            return results, errors

        func.parallel = parallel
        return func

    return decorator
```

Use it like this:

```python
@with_parallel_runner(result_name="price")
def fetch_price(ticker):
    return some_api_call(ticker)
```

Now you get both styles:

```python
fetch_price("AAPL")
fetch_price.parallel(["AAPL", "MSFT", "NVDA"])
```

This is the important design win:

- the single-item function stays honest
- the batch behavior is available when you need it
- the API reads naturally

## Why Not Just Use a Helper Function?

You could absolutely write this:

```python
run_parallel(fetch_price, ["AAPL", "MSFT"])
```

There is nothing wrong with that.

In fact, it is often the simplest solution.

But attaching `.parallel()` has one nice advantage: it keeps related behavior together.

If I am reading code and I see a function called `fetch_price`, I can reasonably guess that:

```python
fetch_price.parallel(...)
```

means "do the same thing, but over many inputs."

That is a clean mental model.

## Feynman Test: Explain It Like a Machine Shop

Imagine a screwdriver.

Its main job is turning screws.

Now imagine someone snaps a little ratcheting attachment onto it.

It is still a screwdriver.
You can still use it in the basic way.
But now it also has a special mode for repeated turning.

That is this pattern.

The function is the screwdriver.
The decorator adds the attachment.
The `.parallel()` method is the ratcheting mode.

You did not throw away the tool.
You gave it an extra capability.

## When This Pattern Is a Good Fit

This pattern works well when:

- the function already has a clear single-item responsibility
- you want to keep that responsibility intact
- you want to add a related convenience operation
- the extra operation is general enough to reuse across many functions

Examples:

- `.parallel()` for API-bound work
- `.retry()` for flaky network calls
- `.cached()` as an attached convenience layer
- `.batch()` for bulk execution of single-item logic

## When It Is a Bad Fit

Do not use this pattern just because it is clever.

It becomes a bad idea when:

- the attached method is surprising
- the extra behavior is only used once
- it makes testing or debugging harder than a plain helper would
- the function starts collecting too many side methods and turns into a junk drawer

If you end up with this:

```python
func.parallel(...)
func.retry(...)
func.cached(...)
func.validated(...)
func.logged(...)
func.profiled(...)
```

then you may be building a mini object system by accident. At that point, a class or a small helper module may be cleaner.

## The Name of the Concept

There is not one single official grand name everyone uses, but the most accurate plain-English description is:

**a decorator that augments a function object**

Two more precise pieces are involved:

- the decorator is a **parameterized decorator**
- `func.parallel = ...` is **attaching an attribute to a function object**

If you want a short phrase, this is a good one:

**function augmentation via decorator**

## Final Takeaway

The whole trick is simple:

1. A Python function is an object.
2. Objects can have attributes.
3. A decorator can attach a new callable attribute to the function.
4. That lets one function expose both a simple mode and a richer mode.

So instead of forcing one function to do too many things, you can keep the main function clean and attach a well-named extra capability beside it.

That is what makes this pattern nice. It respects the original job of the function while giving it a power-up.
