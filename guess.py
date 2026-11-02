"""Игра «Угадай число»: компьютер загадывает число, вы угадываете."""

import random


def play(low: int = 1, high: int = 100) -> int:
    secret = random.randint(low, high)
    attempts = 0
    print(f"Я загадал число от {low} до {high}. Попробуйте угадать!")

    while True:
        try:
            raw = input("Ваш вариант: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\nВыход. Я загадал {secret}.")
            return attempts
        if not raw.lstrip("-").isdigit():
            print("Введите целое число.")
            continue

        guess = int(raw)
        attempts += 1

        if guess < secret:
            print("Больше ↑")
        elif guess > secret:
            print("Меньше ↓")
        else:
            print(f"Угадали! Число {secret}, попыток: {attempts}.")
            return attempts


if __name__ == "__main__":
    play()
