from gpiozero import OutputDevice
from time import sleep
import threading

# =========================================
# CONFIG
# =========================================
STEP_DELAY = 0.002
STEPS_PER_MM = 64   # calibrate for your mechanism

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

# A is correct
# B, C, D are reversed
MOTOR_DIRECTIONS = {
    "A": 1,
    "B": -1,
    "C": -1,
    "D": -1,
}

stop_requested = False


# =========================================
# MOTOR CLASS
# =========================================
class StepperMotor:
    def __init__(self, name, pins, direction=1):
        self.name = name
        self.outputs = [OutputDevice(pin) for pin in pins]
        self.current_level = 0.0
        self.step_index = 0
        self.direction = direction

    def apply_state(self, state):
        for out, value in zip(self.outputs, state):
            if value:
                out.on()
            else:
                out.off()

    def step_one(self, logical_direction):
        actual_direction = logical_direction * self.direction

        if actual_direction > 0:
            self.step_index = (self.step_index + 1) % len(HALF_STEP_SEQ)
        else:
            self.step_index = (self.step_index - 1) % len(HALF_STEP_SEQ)

        self.apply_state(HALF_STEP_SEQ[self.step_index])

    def release(self):
        for out in self.outputs:
            out.off()


# =========================================
# MOTOR SETUP
# =========================================
motors = {
    "A": StepperMotor("A", [17, 27, 22, 23], direction=MOTOR_DIRECTIONS["A"]),
    "B": StepperMotor("B", [24, 25, 5, 6], direction=MOTOR_DIRECTIONS["B"]),
    "C": StepperMotor("C", [12, 13, 16, 20], direction=MOTOR_DIRECTIONS["C"]),
    "D": StepperMotor("D", [21, 26, 19, 18], direction=MOTOR_DIRECTIONS["D"]),
}


# =========================================
# YOUR 10 CONFIGURATIONS
# levels are in feet
# =========================================
CONFIGURATIONS = [
    {"A": 3.0, "B": 3.0, "C": 3.0, "D": 3.0},  # 1
    {"A": 2.0, "B": 3.0, "C": 4.0, "D": 5.0},  # 2
    {"A": 3.0, "B": 3.0, "C": 6.0, "D": 6.0},  # 3
    {"A": 4.0, "B": 7.0, "C": 4.0, "D": 7.0},  # 4
    {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},  # 5
    {"A": 5.0, "B": 5.0, "C": 5.0, "D": 5.0},  # 6
    {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0},  # 7
    {"A": 7.0, "B": 6.0, "C": 5.0, "D": 4.0},  # 8
    {"A": 2.0, "B": 2.0, "C": 2.0, "D": 2.0},  # 9
    {"A": 5.0, "B": 3.0, "C": 3.0, "D": 5.0},  # 10
]


# =========================================
# HELPERS
# =========================================
def level_to_mm(level):
    return int(level * 12)


def mm_to_steps(mm):
    return int(round(mm * STEPS_PER_MM))


def release_all():
    for motor in motors.values():
        motor.release()


def print_status():
    print("\nCurrent levels:")
    for name in ["A", "B", "C", "D"]:
        print(f"  {name} = {motors[name].current_level:g} ft")


# =========================================
# LOW LEVEL STEPPING
# =========================================
def run_steps_for_motor(motor, steps, logical_direction):
    global stop_requested

    for _ in range(steps):
        if stop_requested:
            break
        motor.step_one(logical_direction)
        sleep(STEP_DELAY)

    motor.release()


# =========================================
# SEQUENTIAL MOVEMENT
# order always A -> B -> C -> D
# =========================================
def move_single_motor_to_zero(name):
    global stop_requested

    motor = motors[name]

    if motor.current_level <= 0:
        return

    reverse_mm = level_to_mm(motor.current_level)
    reverse_steps = mm_to_steps(reverse_mm)

    print(f"  {name}: {motor.current_level:g} ft -> 0")
    run_steps_for_motor(motor, reverse_steps, logical_direction=-1)

    if not stop_requested:
        motor.current_level = 0.0


def move_single_motor_to_target(name, target_level):
    global stop_requested

    motor = motors[name]
    target_mm = level_to_mm(target_level)
    target_steps = mm_to_steps(target_mm)

    print(f"  {name}: 0 -> {target_level:g} ft")

    if target_steps > 0:
        run_steps_for_motor(motor, target_steps, logical_direction=1)

    if not stop_requested:
        motor.current_level = target_level


def home_selected(selected_names):
    ordered_names = [name for name in ["A", "B", "C", "D"] if name in selected_names]
    for name in ordered_names:
        if stop_requested:
            break
        move_single_motor_to_zero(name)


def move_selected_to_targets(targets):
    ordered_names = [name for name in ["A", "B", "C", "D"] if name in targets]
    for name in ordered_names:
        if stop_requested:
            break
        move_single_motor_to_target(name, targets[name])


def execute_targets(targets):
    if stop_requested:
        return

    selected_names = list(targets.keys())
    home_selected(selected_names)

    if stop_requested:
        return

    move_selected_to_targets(targets)


def return_all_to_zero():
    global stop_requested

    print("\nReturning all motors to zero...")
    # ignore stop while zeroing out
    old_stop = stop_requested
    stop_requested = False

    for name in ["A", "B", "C", "D"]:
        motor = motors[name]
        if motor.current_level > 0:
            reverse_mm = level_to_mm(motor.current_level)
            reverse_steps = mm_to_steps(reverse_mm)
            print(f"  {name}: {motor.current_level:g} ft -> 0")
            run_steps_for_motor(motor, reverse_steps, logical_direction=-1)
            motor.current_level = 0.0

    release_all()
    stop_requested = old_stop
    print("All motors are at zero.")


# =========================================
# STOP LISTENER
# type "stop" anytime while loop is running
# =========================================
def stop_listener():
    global stop_requested

    while not stop_requested:
        try:
            command = input().strip().lower()
            if command == "stop":
                stop_requested = True
                print("\nStop requested. Finishing safely and returning to zero...")
                break
        except EOFError:
            break


# =========================================
# MAIN LOOP
# =========================================
def main():
    global stop_requested

    print("Loop Motor Sequence Controller")
    print("Type 'start' to begin the infinite configuration loop.")
    print("Once running, type 'stop' and press Enter to stop and return all motors to zero.")
    print("")

    while True:
        cmd = input("Enter command: ").strip().lower()
        if cmd == "start":
            break
        print("Please type 'start' to begin.")

    print("\nStarting loop...")
    print("Type 'stop' and press Enter at any time to stop.\n")

    listener_thread = threading.Thread(target=stop_listener, daemon=True)
    listener_thread.start()

    config_index = 0

    try:
        while not stop_requested:
            current_config = CONFIGURATIONS[config_index]

            print(f"\n--- Configuration {config_index + 1} ---")
            print(current_config)

            execute_targets(current_config)

            if stop_requested:
                break

            print_status()

            config_index = (config_index + 1) % len(CONFIGURATIONS)

    finally:
        return_all_to_zero()
        release_all()
        print("\nLoop stopped. Program exiting.")


if __name__ == "__main__":
    main()
