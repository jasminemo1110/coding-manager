"""参考资料星标置顶：星标状态、加星浮到最上面。"""

import app


def add_ref(test_db, title, starred=0):
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO reference_items (title, body, links, starred) VALUES (?, '', '[]', ?)",
            (title, starred),
        )
        return cur.lastrowid


def test_star_route_toggles(test_db):
    rid = add_ref(test_db, "A")
    client = app.app.test_client()
    assert client.post(f"/reference/{rid}/star").get_json()["starred"] == 1
    assert client.post(f"/reference/{rid}/star").get_json()["starred"] == 0


def test_starred_floats_to_top(test_db):
    a = add_ref(test_db, "参考A")   # 最早
    b = add_ref(test_db, "参考B")
    c = add_ref(test_db, "参考C")   # 最新
    # 默认无星：按 id 倒序 C、B、A
    app.app.test_client().post(f"/reference/{a}/star")  # 给最早的 A 加星
    html = app.app.test_client().get("/notes").get_data(as_text=True)
    # 加星的 A 应排在最前，排在 C、B 之前
    assert html.index("参考A") < html.index("参考C") < html.index("参考B")
