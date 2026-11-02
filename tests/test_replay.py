"""Replay of real readings from 02/11/2026 (Shelly EM, grid channel + PF).

Values copied from Home Assistant history; seconds are relative to the
window start. Solar was about constant during each window, so the grid
channel steps equal the house steps.
"""
import math

from test_engine import NilmEngine


def q_of(p, pf):
    pf = pf / 100
    return math.copysign(p * math.sqrt(1 - pf * pf) / abs(pf), pf)


def run(eng, rows, offset=0):
    for t, p, pf in rows:
        eng.update(offset + t, p, q_of(p, pf))


# 03:05:00 -> 03:08:45  fridge start (+137 W), PF -0.67 -> -0.84
FRIDGE = [
    (0, 258, -67), (1, 281, -67), (2, 265, -67), (17, 266, -67), (32, 259, -67),
    (35, 293, -68), (50, 302, -68), (65, 296, -68), (80, 289, -68), (95, 289, -68),
    (110, 287, -68), (125, 293, -68), (140, 287, -67), (155, 283, -67), (170, 283, -67),
    (173, 466, -85), (175, 419, -84), (180, 448, -83), (195, 425, -84), (210, 420, -83),
    (225, 419, -83),
]

# 08:14:00 -> 08:16:49  toaster (+787 W, resistive)
TOASTER = [
    (0, 149, -39), (11, 143, -38), (22, 133, -36), (36, 140, -37), (42, 139, -37),
    (46, 157, -41), (47, 146, -37), (57, 158, -39), (60, 171, -40), (62, 159, -39),
    (65, 155, -38), (68, 945, -92), (70, 947, -93), (72, 938, -92), (87, 947, -92),
    (102, 959, -91), (117, 960, -92), (132, 943, -93), (147, 937, -91), (160, 929, -91),
    (161, 945, -92), (168, 839, -85), (169, 177, -40), (184, 170, -40), (199, 165, -40),
]


def test_replay_fridge_reactive_ratio():
    eng = NilmEngine(hour_of=lambda ts: 3)
    run(eng, FRIDGE)
    ev = eng.events[-1]
    assert 120 <= ev.power <= 160
    assert ev.q_ratio is not None and 0.15 <= ev.q_ratio <= 0.5


def test_replay_toaster_learn_and_recognise():
    eng = NilmEngine(hour_of=lambda ts: 8)
    run(eng, TOASTER)
    unk = eng.unknown_events()
    assert len(unk) == 1
    ev = unk[0]
    assert 760 <= ev.power <= 820
    assert abs(ev.q_ratio) < 0.12
    assert ev.duration_s and 90 <= ev.duration_s <= 110
    eng.label_event(ev.id, "Tostapane")
    # the second toast of the morning, 4 minutes later
    run(eng, TOASTER, offset=240)
    assert eng.events[-1].label is None
    assert eng.events[-1].appliance == "Tostapane"


def test_replay_fridge_not_confused_with_toaster():
    eng = NilmEngine(hour_of=lambda ts: 8)
    eng.teach("Frigo", 165, reactive_ratio=0.31, duration_minutes=14, samples=3)
    run(eng, TOASTER)
    assert eng.events[-1].appliance is None
