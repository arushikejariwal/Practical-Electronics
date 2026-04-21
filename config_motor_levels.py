from gpiozero import OutputDevice
from time import sleep
import re
import json
import os

# =========================================
# CONFIG
# =========================================
STEP_DELAY = 0.002
STEPS_PER_MM = 64   # calibrate for your mechanism

MIN_LEVEL = 0.0
MAX_LEVEL = 10.0

CONFIG_FILE = "motor_configs.json"

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


# =========================================
# MOTOR CLASS
# =========================================
class StepperMotor:
    def __init__(self, name, pins):
        self.name = name
        self.outputs = [OutputDevice(pin) for pin in pins]
        self.current_level = 0.0
        self.step_index = 0

    def apply_state(self, state):
        for out, value in zip(self.outputs, state):
            if value:
                out.on()
            else:
                out.off()

    def step_one(self, direction):
        if direction > 0:
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
    "A": StepperMotor("A", [17, 27, 22, 23]),
    "B": StepperMotor("B", [24, 25, 5, 6]),
    "C": StepperMotor("C", [12, 13, 16, 20]),
    "D": StepperMotor("D", [21, 26, 19, 18]),
}


# =========================================
# CONFIG FILE HELPERS
# =========================================
def load_configs():
    if not os.path.exists(CONFIG_FILE):
        return {}

    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except Exception:
        return {}


def save_configs(configs):
    with open(CONFIG_FILE, "w") as f:
        json.dump(configs, f, indent=2)


def save_named_config(name, targets):
    configs = load_configs()
    configs[name.lower()] = targets
    save_configs(configs)


def get_named_config(name):
    configs = load_configs()
    return configs.get(name.lower())


def list_configs():
    configs = load_configs()
    if not configs:
        print("No saved configurations found.")
        return

    print("\nSaved configurations:")
    for name, targets in configs.items():
        parts = []
        for motor_name in ["A", "B", "C", "D"]:
            if motor_name in targets:
                parts.append(f"{motor_name}{targets[motor_name]:g}")
        print(f"  {name} -> {' '.join(parts)}")


# =========================================
# HELPERS
# =========================================
def level_to_mm(level):
    return int(level * 12)


def mm_to_steps(mm):
    return int(round(mm * STEPS_PER_MM))


def validate_level(level):
    if level < MIN_LEVEL or level > MAX_LEVEL:
        raise ValueError("Level must be between 0 and 10.")

    if level * 2 != int(level * 2):
        raise ValueError("Only 0.5 increments are allowed.")

    return level


def print_valid_levels():
    print("Valid levels:")
    x = 0.0
    while x <= 10.0001:
        print(f"  {x:g}")
        x += 0.5


def print_status():
    print("\nCurrent motor levels:")
    for name in ["A", "B", "C", "D"]:
        print(f"  {name} = {motors[name].current_level:g} ft")


def release_all():
    for m in motors.values():
        m.release()


# =========================================
# PARSING
# Accepted:
#   A2
#   A=2
#   A2 B5 C5 D6
#   A=2, B=5, C=5, D=6
#   ALL2
#   ALL=2.5
#   CONFIGbanana
# =========================================
def parse_command(command_text):
    text = command_text.strip().upper()

    if not text:
        raise ValueError("Empty command.")

    match_config = re.fullmatch(r"CONFIG([A-Z0-9_]+)", text)
    if match_config:
        config_name = match_config.group(1).lower()
        saved = get_named_config(config_name)
        if saved is None:
            raise ValueError(f"No saved configuration named '{config_name}'.")
        return saved, True

    tokens = re.split(r"[,\s]+", text)
    tokens = [t for t in tokens if t]

    results = {}

    for token in tokens:
        match_all = re.fullmatch(r"ALL=?([0-9]+(?:\.5)?)", token)
        if match_all:
            level = float(match_all.group(1))
            validate_level(level)
            for name in ["A", "B", "C", "D"]:
                results[name] = level
            continue

        match_single = re.fullmatch(r"([ABCD])=?([0-9]+(?:\.5)?)", token)
        if match_single:
            motor_name = match_single.group(1)
            level = float(match_single.group(2))
            validate_level(level)
            results[motor_name] = level
            continue

        raise ValueError(
            f"Invalid token '{token}'. Use A2, A=2, A2 B5 C5 D6, ALL2, or CONFIGname"
        )

    if not results:
        raise ValueError("No valid motor commands found.")

    return results, False


# =========================================
# GROUP STEPPING
# =========================================
def run_group_steps(step_plan, direction):
    remaining = dict(step_plan)

    if not remaining:
        return

    while any(steps > 0 for steps in remaining.values()):
        for motor, steps_left in remaining.items():
            if steps_left > 0:
                motor.step_one(direction)
                remaining[motor] -= 1
        sleep(STEP_DELAY)

    for motor in remaining.keys():
        motor.release()


# =========================================
# HOMING + MOVING
# =========================================
def home_selected(selected_names):
    reverse_plan = {}

    print("\nHoming selected motors to 0...")
    for name in selected_names:
        motor = motors[name]
        if motor.current_level > 0:
            reverse_mm = level_to_mm(motor.current_level)
            reverse_steps = mm_to_steps(reverse_mm)
            reverse_plan[motor] = reverse_steps
            print(f"  {name}: {motor.current_level:g} ft -> 0 ({reverse_mm} mm, {reverse_steps} steps)")
        else:
            print(f"  {name}: already at 0")

    run_group_steps(reverse_plan, direction=-1)

    for name in selected_names:
        motors[name].current_level = 0.0

    print("Selected motors returned to 0.")


def move_selected_to_targets(targets):
    forward_plan = {}

    print("\nMoving selected motors to targets...")
    for name, target_level in targets.items():
        motor = motors[name]
        target_mm = level_to_mm(target_level)
        target_steps = mm_to_steps(target_mm)

        if target_steps > 0:
            forward_plan[motor] = target_steps

        print(f"  {name}: 0 -> {target_level:g} ft ({target_mm} mm, {target_steps} steps)")

    run_group_steps(forward_plan, direction=1)

    for name, target_level in targets.items():
        motors[name].current_level = target_level

    print("Selected motors reached new targets.")


def execute_command(targets):
    selected_names = list(targets.keys())

    print("\n====================================")
    print("Command received:")
    for name in selected_names:
        print(f"  {name} -> {targets[name]:g} ft")

    print_status()
    home_selected(selected_names)
    move_selected_to_targets(targets)
    print_status()
    print("====================================")


# =========================================
# SAVE PROMPT
# Now asks for saving for any fresh command,
# including single-motor commands.
# =========================================
def maybe_save_configuration(original_targets, came_from_saved_config):
    if came_from_saved_config:
        return

    answer = input("Do you want to save this configuration? (y/n): ").strip().lower()
    if answer not in ("y", "yes"):
        return

    while True:
        name = input("Enter configuration name: ").strip().lower()

        if not name:
            print("Name cannot be empty.")
            continue

        if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
            print("Use only letters, numbers, or underscores.")
            continue

        existing = get_named_config(name)
        if existing is not None:
            overwrite = input(f"Configuration '{name}' already exists. Overwrite? (y/n): ").strip().lower()
            if overwrite not in ("y", "yes"):
                continue

        save_named_config(name, original_targets)
        print(f"Configuration saved as: config{name}")
        break


# =========================================
# MAIN
# =========================================
def main():
    print("4-Motor 28BYJ-48 / ULN2003 Controller")
    print("Motors: A, B, C, D")
    print("Allowed levels: 0, 0.5, 1, 1.5 ... 10")
    print("")
    print("Accepted inputs:")
    print("  A2")
    print("  A=2")
    print("  A2 B5 C5 D6")
    print("  A=2, B=5, C=5, D=6")
    print("  ALL2")
    print("  ALL=2.5")
    print("  CONFIGbanana")
    print("")
    print("Commands:")
    print("  list     -> show valid levels")
    print("  status   -> show current levels")
    print("  configs  -> list saved configurations")
    print("  quit     -> exit")
    print("")

    try:
        while True:
            user_input = input("Enter command: ").strip()

            if not user_input:
                continue

            low = user_input.lower()

            if low in ("quit", "exit", "q"):
                break

            if low == "list":
                print_valid_levels()
                continue

            if low == "status":
                print_status()
                continue

            if low == "configs":
                list_configs()
                continue

            try:
                targets, came_from_saved_config = parse_command(user_input)
                execute_command(targets)
                maybe_save_configuration(targets, came_from_saved_config)
            except Exception as e:
                print("Error:", e)

    finally:
        release_all()
        print("All motors released.")


if __name__ == "__main__":
    main()
