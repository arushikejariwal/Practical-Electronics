from gpiozero import OutputDevice
from time import sleep

# BCM pin numbering
IN1_PIN = 17
IN2_PIN = 27
IN3_PIN = 22
IN4_PIN = 23

# ULN2003 inputs
in1 = OutputDevice(IN1_PIN)
in2 = OutputDevice(IN2_PIN)
in3 = OutputDevice(IN3_PIN)
in4 = OutputDevice(IN4_PIN)

pins = [in1, in2, in3, in4]

# 28BYJ-48 common 8-step half-step sequence
HALF_STEP_SEQ = [
    (1, 0, 0, 0),
    (1, 1, 0, 0),
    (0, 1, 0, 0),
    (0, 1, 1, 0),
    (0, 0, 1, 0),
    (0, 0, 1, 1),
    (0, 0, 0, 1),
    (1, 0, 0, 1),
]

# -------- Calibration --------
# 1 foot input = 12 mm travel in your model
# Change this after testing your real mechanism
STEPS_PER_MM = 64
STEP_DELAY = 0.002  # seconds between half-steps

MIN_LEVEL = 0.0
MAX_LEVEL = 10.0

current_level = 0.0  # remembers previous commanded position


def set_coils(state):
    for pin, value in zip(pins, state):
        if value:
            pin.on()
        else:
            pin.off()


def motor_off():
    for pin in pins:
        pin.off()


def step_motor(steps, direction=1):
    seq = HALF_STEP_SEQ if direction > 0 else list(reversed(HALF_STEP_SEQ))
    for i in range(steps):
        set_coils(seq[i % len(seq)])
        sleep(STEP_DELAY)
    motor_off()


def parse_level(text):
    text = text.strip()

    try:
        level = float(text)
    except ValueError:
        raise ValueError("Invalid input. Use only: 0, 0.5, 1, 1.5 ... 10")

    if level < MIN_LEVEL or level > MAX_LEVEL:
        raise ValueError("Level must be between 0 and 10")

    # only allow 0.5 increments
    if level * 2 != int(level * 2):
        raise ValueError("Only 0.5 increments are allowed")

    return level


def level_to_mm(level):
    # 1 foot = 12 mm in your model
    return int(level * 12)


def mm_to_steps(mm):
    return int(round(mm * STEPS_PER_MM))


def print_valid_levels():
    print("Valid levels:")
    level = 0.0
    while level <= 10.0001:
        print(f"  {level:g}")
        level += 0.5


def home_by_reversing_previous():
    global current_level

    if current_level == 0:
        print("Already at 0")
        return

    reverse_mm = level_to_mm(current_level)
    reverse_steps = mm_to_steps(reverse_mm)

    print(f"Returning to 0 from {current_level:g} ft")
    print(f"Reverse travel: {reverse_mm} mm")
    print(f"Reverse steps: {reverse_steps}")

    # reverse direction back to zero
    step_motor(reverse_steps, direction=-1)
    current_level = 0.0
    print("Returned to 0")


def move_to_level(new_level):
    global current_level

    target_mm = level_to_mm(new_level)
    target_steps = mm_to_steps(target_mm)

    print("\n----------------------")
    print(f"Previous level: {current_level:g} ft")
    print(f"New requested level: {new_level:g} ft")
    print(f"Target travel from 0: {target_mm} mm")
    print(f"Target steps from 0: {target_steps}")

    # always go back to zero first
    home_by_reversing_previous()

    if target_steps > 0:
        print("Moving to new target...")
        step_motor(target_steps, direction=1)

    current_level = new_level
    print(f"Move complete. Current level = {current_level:g} ft")


def main():
    print("28BYJ-48 / ULN2003 level controller")
    print("Accepted inputs: 0, 0.5, 1, 1.5 ... 10")
    print("Each new input first reverses previous motion back to 0, then moves to the new level")
    print("Type 'list' to see valid levels")
    print("Type 'zero' to return to 0")
    print("Type 'quit' to stop")

    try:
        while True:
            user_input = input("\nEnter target level: ").strip().lower()

            if user_input in ("quit", "exit", "q"):
                break

            if user_input == "list":
                print_valid_levels()
                continue

            if user_input == "zero":
                home_by_reversing_previous()
                continue

            try:
                level = parse_level(user_input)
                move_to_level(level)
            except Exception as e:
                print("Error:", e)

    finally:
        motor_off()
        print("Motor off")


if __name__ == "__main__":
    main()
