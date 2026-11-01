import ast
from pathlib import Path


def test_single_leg_sequence():
    tree = ast.parse((Path(__file__).parents[1] / "core/calculator.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "select_tick_actions")
    scope = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), scope)
    select = scope["select_tick_actions"]
    cut = {"type": "ORDER", "key": "BASE_LONG", "is_reduction": True}
    short = {"type": "ORDER", "key": "BASE_SHORT"}
    long = {"type": "ORDER", "key": "BASE_LONG"}
    virtual = {"type": "VIRTUAL_ORDER"}
    assert select([short, cut, virtual], False) == [cut, virtual]
    assert select([long, short], False, "BASE_SHORT") == [short]
    assert select([long, short], False, "BASE_LONG") == [long]
    assert select([long, short], True) == [long, short]
    short_cut = dict(short, is_reduction=True)
    assert select([cut, short_cut], False) == [cut]


if __name__ == "__main__":
    test_single_leg_sequence()
    print("PASS: single-leg sequence")
