from gpiozero import OutputDevice
from time import sleep
from flask import Flask, request, redirect, url_for, render_template_string
import json
import os
import re

app = Flask(__name__)

# =========================================
# CONFIG
# =========================================
STEP_DELAY = 0.002
STEPS_PER_MM = 64   # calibrate for your mechanism
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

VALID_LEVELS = [x * 0.5 for x in range(0, 21)]

is_standby = False
last_message = "System ready."


# =========================================
# MOTOR CLASS
# direction = +1 means normal
# direction = -1 means reversed
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
        # logical_direction:
        #   +1 = move toward target
        #   -1 = move back to zero
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
# A is correct as-is
# B, C, D are reversed
# =========================================
motors = {
    "A": StepperMotor("A", [17, 27, 22, 23], direction=1),
    "B": StepperMotor("B", [24, 25, 5, 6], direction=-1),
    "C": StepperMotor("C", [12, 13, 16, 20], direction=-1),
    "D": StepperMotor("D", [21, 26, 19, 18], direction=-1),
}


# =========================================
# CONFIG STORAGE
# =========================================
def load_configs():
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
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
    return load_configs().get(name.lower())


def delete_named_config(name):
    configs = load_configs()
    if name.lower() in configs:
        del configs[name.lower()]
        save_configs(configs)


# =========================================
# HELPERS
# =========================================
def set_message(msg):
    global last_message
    last_message = msg


def level_to_mm(level):
    return int(level * 12)


def mm_to_steps(mm):
    return int(round(mm * STEPS_PER_MM))


def validate_level(level):
    if level not in VALID_LEVELS:
        raise ValueError("Only 0.5 increments from 0 to 10 are allowed.")
    return level


def release_all():
    for motor in motors.values():
        motor.release()


def get_status_data():
    return {
        "A": motors["A"].current_level,
        "B": motors["B"].current_level,
        "C": motors["C"].current_level,
        "D": motors["D"].current_level,
    }


# =========================================
# LOW-LEVEL STEPPING
# =========================================
def run_group_steps(step_plan, logical_direction):
    remaining = dict(step_plan)
    if not remaining:
        return

    while any(steps > 0 for steps in remaining.values()):
        for motor, steps_left in remaining.items():
            if steps_left > 0:
                motor.step_one(logical_direction)
                remaining[motor] -= 1
        sleep(STEP_DELAY)

    for motor in remaining.keys():
        motor.release()


# =========================================
# SEQUENTIAL MOTOR MOTION
# Group commands run A -> B -> C -> D
# =========================================
def move_single_motor_to_zero(name):
    motor = motors[name]

    if motor.current_level <= 0:
        return

    reverse_mm = level_to_mm(motor.current_level)
    reverse_steps = mm_to_steps(reverse_mm)

    step_plan = {motor: reverse_steps}
    run_group_steps(step_plan, logical_direction=-1)

    motor.current_level = 0.0
    motor.release()


def move_single_motor_to_target(name, target_level):
    motor = motors[name]

    target_mm = level_to_mm(target_level)
    target_steps = mm_to_steps(target_mm)

    if target_steps > 0:
        step_plan = {motor: target_steps}
        run_group_steps(step_plan, logical_direction=1)

    motor.current_level = target_level
    motor.release()


def home_selected(selected_names):
    ordered_names = [name for name in ["A", "B", "C", "D"] if name in selected_names]
    for name in ordered_names:
        move_single_motor_to_zero(name)


def move_selected_to_targets(targets):
    ordered_names = [name for name in ["A", "B", "C", "D"] if name in targets]
    for name in ordered_names:
        move_single_motor_to_target(name, targets[name])


def execute_targets(targets):
    selected_names = list(targets.keys())
    home_selected(selected_names)
    move_selected_to_targets(targets)


def return_all_to_zero():
    home_selected(["A", "B", "C", "D"])
    release_all()


# =========================================
# COMMAND PARSING
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
        return saved

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

    return results


def build_targets_from_form(form):
    targets = {}

    if form.get("use_A"):
        targets["A"] = validate_level(float(form.get("level_A", "0")))
    if form.get("use_B"):
        targets["B"] = validate_level(float(form.get("level_B", "0")))
    if form.get("use_C"):
        targets["C"] = validate_level(float(form.get("level_C", "0")))
    if form.get("use_D"):
        targets["D"] = validate_level(float(form.get("level_D", "0")))

    return targets


# =========================================
# HTML
# =========================================
PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Motor Control</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 24px;
      background: #f5f5f5;
      color: #222;
    }
    .wrap {
      max-width: 1100px;
      margin: auto;
    }
    .card {
      background: white;
      border-radius: 14px;
      padding: 18px;
      margin-bottom: 18px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.08);
    }
    h1, h2, h3 { margin-top: 0; }
    .row {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
    }
    .motor {
      border: 1px solid #ddd;
      border-radius: 12px;
      padding: 12px;
      background: #fafafa;
    }
    label {
      display: block;
      margin-bottom: 8px;
    }
    select, input[type="text"] {
      width: 100%;
      padding: 10px;
      border-radius: 8px;
      border: 1px solid #bbb;
      box-sizing: border-box;
    }
    button {
      padding: 10px 14px;
      border: none;
      border-radius: 10px;
      background: #222;
      color: white;
      cursor: pointer;
      margin-right: 8px;
      margin-top: 8px;
    }
    button.secondary {
      background: #666;
    }
    button.warn {
      background: #9b1c1c;
    }
    .status-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
    }
    .pill {
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: #eee;
      font-size: 14px;
      margin-right: 8px;
    }
    .standby-on { background: #ffe0e0; }
    .standby-off { background: #e0ffe8; }
    .config-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border: 1px solid #ddd;
      border-radius: 10px;
      padding: 10px;
      margin-bottom: 10px;
      background: #fafafa;
      gap: 12px;
      flex-wrap: wrap;
    }
    .small-form {
      display: inline;
    }
    .msg {
      font-weight: bold;
      padding: 12px;
      background: #eef4ff;
      border-radius: 10px;
    }
    .footer-note {
      font-size: 14px;
      color: #555;
    }
  </style>
</head>
<body>
<div class="wrap">
  <h1>4-Motor Control Panel</h1>

  <div class="card">
    <div class="msg">{{ message }}</div>
    <p>
      <span class="pill {{ 'standby-on' if standby else 'standby-off' }}">
        Standby: {{ 'ON' if standby else 'OFF' }}
      </span>
    </p>
  </div>

  <div class="card">
    <h2>Current Levels</h2>
    <div class="status-grid">
      {% for name, level in status.items() %}
        <div class="motor">
          <h3>Motor {{ name }}</h3>
          <div>{{ level }} ft</div>
        </div>
      {% endfor %}
    </div>
  </div>

  <div class="card">
    <h2>Run Motors</h2>
    <form method="post" action="/run">
      <div class="row">
        {% for name in ['A','B','C','D'] %}
        <div class="motor">
          <h3>{{ name }}</h3>
          <label><input type="checkbox" name="use_{{ name }}"> Include motor {{ name }}</label>
          <label>Level</label>
          <select name="level_{{ name }}">
            {% for level in valid_levels %}
              <option value="{{ level }}">{{ level }}</option>
            {% endfor %}
          </select>
        </div>
        {% endfor %}
      </div>
      <button type="submit">Run Selected Motors</button>
    </form>
  </div>

  <div class="card">
    <h2>Run All Motors Together</h2>
    <form method="post" action="/run_all">
      <label>Level for A, B, C, and D</label>
      <select name="all_level">
        {% for level in valid_levels %}
          <option value="{{ level }}">{{ level }}</option>
        {% endfor %}
      </select>
      <button type="submit">Run ALL</button>
    </form>
  </div>

  <div class="card">
    <h2>Command Line Style Input</h2>
    <form method="post" action="/run_command">
      <label>Examples: A2 B5 C5 D6, A=2, ALL2, configbanana</label>
      <input type="text" name="command" placeholder="Enter command">
      <button type="submit">Run Command</button>
    </form>
  </div>

  <div class="card">
    <h2>Save Current Selection as Config</h2>
    <form method="post" action="/save_form_config">
      <div class="row">
        {% for name in ['A','B','C','D'] %}
        <div class="motor">
          <h3>{{ name }}</h3>
          <label><input type="checkbox" name="use_{{ name }}"> Include motor {{ name }}</label>
          <label>Level</label>
          <select name="level_{{ name }}">
            {% for level in valid_levels %}
              <option value="{{ level }}">{{ level }}</option>
            {% endfor %}
          </select>
        </div>
        {% endfor %}
      </div>
      <label style="margin-top:16px;">Configuration Name</label>
      <input type="text" name="config_name" placeholder="banana">
      <button type="submit">Save Config</button>
    </form>
  </div>

  <div class="card">
    <h2>Saved Configurations</h2>
    {% if configs %}
      {% for name, targets in configs.items() %}
        <div class="config-item">
          <div>
            <strong>{{ name }}</strong><br>
            {% for motor, level in targets.items() %}
              <span class="pill">{{ motor }}{{ level }}</span>
            {% endfor %}
          </div>
          <div>
            <form class="small-form" method="post" action="/run_config">
              <input type="hidden" name="config_name" value="{{ name }}">
              <button type="submit">Run</button>
            </form>
            <form class="small-form" method="post" action="/delete_config">
              <input type="hidden" name="config_name" value="{{ name }}">
              <button class="warn" type="submit">Delete</button>
            </form>
          </div>
        </div>
      {% endfor %}
    {% else %}
      <p>No saved configs yet.</p>
    {% endif %}
  </div>

  <div class="card">
    <h2>System Controls</h2>
    <form method="post" action="/standby" style="display:inline;">
      <button class="secondary" type="submit">Standby</button>
    </form>
    <form method="post" action="/unstandby" style="display:inline;">
      <button class="secondary" type="submit">Un-Standby</button>
    </form>
    <form method="post" action="/zero_all" style="display:inline;">
      <button type="submit">Return All to Zero</button>
    </form>
    <form method="post" action="/safe_quit" style="display:inline;">
      <button class="warn" type="submit">Safe Quit</button>
    </form>
    <p class="footer-note">
      Group commands run sequentially in this version: A → B → C → D.
    </p>
  </div>
</div>
</body>
</html>
"""


# =========================================
# ROUTES
# =========================================
@app.route("/")
def index():
    return render_template_string(
        PAGE,
        status=get_status_data(),
        standby=is_standby,
        message=last_message,
        configs=load_configs(),
        valid_levels=VALID_LEVELS,
    )


@app.route("/run", methods=["POST"])
def run():
    global is_standby
    try:
        if is_standby:
            raise ValueError("System is in standby. Un-standby first.")

        targets = build_targets_from_form(request.form)
        if not targets:
            raise ValueError("Select at least one motor.")

        execute_targets(targets)

        if len(targets) > 1:
            set_message(f"Ran motors sequentially A → B → C → D where selected: {targets}")
        else:
            set_message(f"Ran motor: {targets}")

    except Exception as e:
        set_message(f"Error: {e}")
    return redirect(url_for("index"))


@app.route("/run_all", methods=["POST"])
def run_all():
    global is_standby
    try:
        if is_standby:
            raise ValueError("System is in standby. Un-standby first.")

        level = validate_level(float(request.form.get("all_level", "0")))
        targets = {"A": level, "B": level, "C": level, "D": level}
        execute_targets(targets)
        set_message(f"Ran ALL motors to {level} ft sequentially: A → B → C → D")

    except Exception as e:
        set_message(f"Error: {e}")
    return redirect(url_for("index"))


@app.route("/run_command", methods=["POST"])
def run_command():
    global is_standby
    try:
        if is_standby:
            raise ValueError("System is in standby. Un-standby first.")

        command = request.form.get("command", "").strip()
        targets = parse_command(command)
        execute_targets(targets)

        if len(targets) > 1:
            set_message(f"Ran command sequentially: {command}")
        else:
            set_message(f"Ran command: {command}")

    except Exception as e:
        set_message(f"Error: {e}")
    return redirect(url_for("index"))


@app.route("/save_form_config", methods=["POST"])
def save_form_config():
    try:
        config_name = request.form.get("config_name", "").strip().lower()
        if not config_name:
            raise ValueError("Configuration name is required.")
        if not re.fullmatch(r"[a-zA-Z0-9_]+", config_name):
            raise ValueError("Use only letters, numbers, or underscores for config names.")

        targets = build_targets_from_form(request.form)
        if not targets:
            raise ValueError("Select at least one motor for the config.")

        save_named_config(config_name, targets)
        set_message(f"Saved configuration: config{config_name}")

    except Exception as e:
        set_message(f"Error: {e}")
    return redirect(url_for("index"))


@app.route("/run_config", methods=["POST"])
def run_config():
    global is_standby
    try:
        if is_standby:
            raise ValueError("System is in standby. Un-standby first.")

        config_name = request.form.get("config_name", "").strip().lower()
        targets = get_named_config(config_name)
        if not targets:
            raise ValueError(f"No config named '{config_name}'")

        execute_targets(targets)

        if len(targets) > 1:
            set_message(f"Ran configuration sequentially: config{config_name}")
        else:
            set_message(f"Ran configuration: config{config_name}")

    except Exception as e:
        set_message(f"Error: {e}")
    return redirect(url_for("index"))


@app.route("/delete_config", methods=["POST"])
def delete_config():
    try:
        config_name = request.form.get("config_name", "").strip().lower()
        delete_named_config(config_name)
        set_message(f"Deleted configuration: config{config_name}")

    except Exception as e:
        set_message(f"Error: {e}")
    return redirect(url_for("index"))


@app.route("/standby", methods=["POST"])
def standby():
    global is_standby
    is_standby = True
    release_all()
    set_message("Standby ON. Movement commands are blocked.")
    return redirect(url_for("index"))


@app.route("/unstandby", methods=["POST"])
def unstandby():
    global is_standby
    is_standby = False
    set_message("Standby OFF. Movement commands are enabled.")
    return redirect(url_for("index"))


@app.route("/zero_all", methods=["POST"])
def zero_all():
    try:
        return_all_to_zero()
        set_message("All motors returned to zero sequentially: A → B → C → D")
    except Exception as e:
        set_message(f"Error: {e}")
    return redirect(url_for("index"))


@app.route("/safe_quit", methods=["POST"])
def safe_quit():
    try:
        return_all_to_zero()
        set_message("All motors returned to zero. You can now stop the app safely.")
    except Exception as e:
        set_message(f"Error: {e}")
    return redirect(url_for("index"))


if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    finally:
        release_all()
