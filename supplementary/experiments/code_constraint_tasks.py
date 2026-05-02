#!/usr/bin/env python3
"""
Task definitions for code constraint experiment.

12 tasks with constant medium difficulty.
Each task has:
- description: what to implement
- function_name: expected function name
- test_code: deterministic test cases

Tasks stay constant across tiers; only format constraints vary.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class CodeTask:
    """A code generation task with tests."""
    task_id: str
    description: str
    function_name: str
    test_code: str


# 12 tasks of medium difficulty (5-15 line solutions typically)
TASKS: List[CodeTask] = [
    # Task 1: Factorial
    CodeTask(
        task_id="factorial",
        description="Write a function `factorial(n)` that returns the factorial of a non-negative integer n.",
        function_name="factorial",
        test_code="""
assert factorial(0) == 1
assert factorial(1) == 1
assert factorial(5) == 120
assert factorial(10) == 3628800
""",
    ),

    # Task 2: Fibonacci
    CodeTask(
        task_id="fibonacci",
        description="Write a function `fibonacci(n)` that returns the nth Fibonacci number (0-indexed, so fibonacci(0)=0, fibonacci(1)=1).",
        function_name="fibonacci",
        test_code="""
assert fibonacci(0) == 0
assert fibonacci(1) == 1
assert fibonacci(10) == 55
assert fibonacci(15) == 610
""",
    ),

    # Task 3: Palindrome check
    CodeTask(
        task_id="is_palindrome",
        description="Write a function `is_palindrome(s)` that returns True if string s is a palindrome (ignoring case), False otherwise.",
        function_name="is_palindrome",
        test_code="""
assert is_palindrome("racecar") == True
assert is_palindrome("RaceCar") == True
assert is_palindrome("hello") == False
assert is_palindrome("A") == True
assert is_palindrome("") == True
""",
    ),

    # Task 4: Sum of digits
    CodeTask(
        task_id="sum_digits",
        description="Write a function `sum_digits(n)` that returns the sum of all digits in a non-negative integer n.",
        function_name="sum_digits",
        test_code="""
assert sum_digits(0) == 0
assert sum_digits(123) == 6
assert sum_digits(9999) == 36
assert sum_digits(10001) == 2
""",
    ),

    # Task 5: Count vowels
    CodeTask(
        task_id="count_vowels",
        description="Write a function `count_vowels(s)` that returns the count of vowels (a, e, i, o, u) in string s (case-insensitive).",
        function_name="count_vowels",
        test_code="""
assert count_vowels("hello") == 2
assert count_vowels("AEIOU") == 5
assert count_vowels("xyz") == 0
assert count_vowels("") == 0
""",
    ),

    # Task 6: Reverse words
    CodeTask(
        task_id="reverse_words",
        description="Write a function `reverse_words(s)` that reverses the order of words in a string s. Words are separated by spaces.",
        function_name="reverse_words",
        test_code="""
assert reverse_words("hello world") == "world hello"
assert reverse_words("a b c") == "c b a"
assert reverse_words("single") == "single"
assert reverse_words("") == ""
""",
    ),

    # Task 7: Find maximum
    CodeTask(
        task_id="find_max",
        description="Write a function `find_max(lst)` that returns the maximum value in a non-empty list of numbers.",
        function_name="find_max",
        test_code="""
assert find_max([1, 2, 3]) == 3
assert find_max([5]) == 5
assert find_max([-1, -5, -2]) == -1
assert find_max([3, 1, 4, 1, 5, 9, 2, 6]) == 9
""",
    ),

    # Task 8: Remove duplicates
    CodeTask(
        task_id="remove_duplicates",
        description="Write a function `remove_duplicates(lst)` that returns a new list with duplicates removed, preserving the original order of first occurrences.",
        function_name="remove_duplicates",
        test_code="""
assert remove_duplicates([1, 2, 2, 3, 1]) == [1, 2, 3]
assert remove_duplicates([1, 1, 1]) == [1]
assert remove_duplicates([]) == []
assert remove_duplicates([1, 2, 3]) == [1, 2, 3]
""",
    ),

    # Task 9: FizzBuzz single
    CodeTask(
        task_id="fizzbuzz",
        description="Write a function `fizzbuzz(n)` that returns 'FizzBuzz' if n is divisible by both 3 and 5, 'Fizz' if divisible by 3 only, 'Buzz' if divisible by 5 only, otherwise the string representation of n.",
        function_name="fizzbuzz",
        test_code="""
assert fizzbuzz(15) == "FizzBuzz"
assert fizzbuzz(9) == "Fizz"
assert fizzbuzz(10) == "Buzz"
assert fizzbuzz(7) == "7"
assert fizzbuzz(30) == "FizzBuzz"
""",
    ),

    # Task 10: GCD
    CodeTask(
        task_id="gcd",
        description="Write a function `gcd(a, b)` that returns the greatest common divisor of two positive integers a and b.",
        function_name="gcd",
        test_code="""
assert gcd(12, 8) == 4
assert gcd(17, 13) == 1
assert gcd(100, 25) == 25
assert gcd(7, 7) == 7
""",
    ),

    # Task 11: Power function
    CodeTask(
        task_id="power",
        description="Write a function `power(base, exp)` that returns base raised to the power exp, where exp is a non-negative integer.",
        function_name="power",
        test_code="""
assert power(2, 3) == 8
assert power(5, 0) == 1
assert power(3, 4) == 81
assert power(10, 2) == 100
""",
    ),

    # Task 12: Count occurrences
    CodeTask(
        task_id="count_occurrences",
        description="Write a function `count_occurrences(lst, target)` that returns the number of times target appears in list lst.",
        function_name="count_occurrences",
        test_code="""
assert count_occurrences([1, 2, 2, 3, 2], 2) == 3
assert count_occurrences([1, 2, 3], 5) == 0
assert count_occurrences([], 1) == 0
assert count_occurrences(["a", "b", "a"], "a") == 2
""",
    ),
]


def get_task_by_id(task_id: str) -> CodeTask:
    """Get a task by its ID."""
    for task in TASKS:
        if task.task_id == task_id:
            return task
    raise ValueError(f"Unknown task: {task_id}")


if __name__ == "__main__":
    print(f"Defined {len(TASKS)} tasks:")
    for task in TASKS:
        print(f"  - {task.task_id}: {task.description[:50]}...")
