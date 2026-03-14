def factorial_iterative(n):
    if not isinstance(n, int) or n < 0:
        raise ValueError("Input must be a non-negative integer")
    
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result

# SIX SEVEN
number = 67
print(f"The factorial of {number} is {factorial_iterative(number)}")
