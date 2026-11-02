"""Unit tests for the pure-Python engine (run with: python -m pytest tests)."""
import importlib.util
import pathlib
import sys

_path = pathlib.Path(__file__).parents[1] / "custom_components/home_nilm/engine.py"
_spec = importlib.util.spec_from_file_location("engine", _path)
engine = importlib.util.module_from_spec(_spec)
sys.modules["engine"] = engine
_spec.loader.exec_module(engine)

NilmEngine = engine.NilmEngine


def feed(eng, start, end, power, q=None, every=5, var=False):
    t = start
    while t < end:
        eng.update(t, power, q, var)
        t += every
    return t


def make():
    return NilmEngine(hour_of=lambda ts: int(ts // 3600) % 24)


def test_step_needs_persistence():
    eng = make()
    t = feed(eng, 0, 300, 250)
    eng.update(t, 900)  # 1 reading spike
    t = feed(eng, t + 2, t + 120, 250)
    assert not eng.events


def test_unknown_then_label_then_recognised():
    eng = make()
    t = feed(eng, 0, 600, 250, q=-300)
    t = feed(eng, t, t + 180, 1030, q=-330)  # toaster on (~780 W, resistive)
    t = feed(eng, t, t + 300, 250, q=-300)  # off
    unk = eng.unknown_events()
    assert len(unk) == 1 and abs(unk[0].power - 780) < 5
    assert unk[0].duration_s and 170 <= unk[0].duration_s <= 190
    eng.label_event(unk[0].id, "Tostapane")
    t = feed(eng, t, t + 200, 1020, q=-330)
    assert eng.events[-1].appliance == "Tostapane"
    assert round(eng.appliance_power("Tostapane")) > 700
    t = feed(eng, t, t + 120, 250, q=-300)
    assert eng.appliance_power("Tostapane") == 0
    assert eng.energy_kwh["Tostapane"] > 0


def test_reactive_separates_fridge_from_coffee():
    eng = make()
    eng.teach("Frigo", 170, reactive_ratio=0.31, duration_minutes=14, samples=3)
    eng.teach("Caffè accensione", 190, reactive_ratio=0.0, samples=3)
    t = feed(eng, 0, 600, 280, q=-310)
    t = feed(eng, t, t + 120, 455, q=-255)  # +175 W, +55 var -> fridge
    assert eng.events[-1].appliance == "Frigo"
    t = feed(eng, t, t + 120, 280, q=-310)
    t = feed(eng, t, t + 120, 470, q=-310)  # +190 W, 0 var -> coffee
    assert eng.events[-1].appliance == "Caffè accensione"


def test_variable_load_suppresses_unknowns():
    eng = make()
    t = feed(eng, 0, 600, 250)
    t = feed(eng, t, t + 600, 1550, var=True)
    assert not eng.unknown_events()
    assert eng.powers()[NilmEngine.VARIABLE] > 1000


def test_persistence_roundtrip():
    eng = make()
    eng.teach("Phon", 1500, reactive_ratio=0.0)
    t = feed(eng, 0, 200, 300)
    t = feed(eng, t, t + 100, 800)
    data = eng.to_dict()
    eng2 = make()
    eng2.load_dict(data)
    assert "Phon" in eng2.signatures
    assert len(eng2.events) == len(eng.events)


def test_rename_and_forget():
    eng = make()
    eng.teach("A", 500)
    assert eng.rename("A", "B")
    assert "B" in eng.signatures and "A" not in eng.signatures
    assert eng.forget("B")
    assert not eng.signatures
