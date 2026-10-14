from server import mail
from server.accounts import Accounts
from server.nudges import DAY, FREE_DAYS, Nudges, free_status
from server.store import Store


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html="", wait=False):
        self.sent.append((to, subject, text))


def _setup(tmp_path):
    acc = Accounts(Store(str(tmp_path)), "studio@x.com")
    m = FakeMailer()
    return acc, m, Nudges(acc, m, "https://example.test")


def _age(acc, user_id, days):
    """Pretend the account and its free credits are this many days old."""
    with acc._db() as db:
        db.execute("UPDATE users SET created=created-? WHERE id=?", (days * DAY, user_id))
        db.execute("DROP TRIGGER ledger_no_update")
        db.execute("UPDATE ledger SET created=created-? WHERE user_id=?", (days * DAY, user_id))
        db.execute("CREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger BEGIN SELECT RAISE(ABORT, 'no'); END")


def test_unconfirmed_get_a_reminder_every_three_days_until_day_14(tmp_path):
    import time
    acc, m, n = _setup(tmp_path)
    u = acc.sign_up("a@b.in", "longpassword1", "A", "1.1.1.1", terms="x")
    now = time.time()
    n.run(now)
    assert m.sent == []                                          # nothing on day 0
    for day in (3, 4, 6, 9, 12, 14, 15, 18):
        n.run(now + day * DAY)
    subjects = [s for _, s, _ in m.sent]
    assert len(subjects) == 5 and subjects[-1].startswith("Last reminder")       # days 3, 6, 9, 12 and 14 only
    assert "/verify/" in m.sent[0][2]


def test_free_credits_reminders_stop_once_used_and_end_after_15_days(tmp_path):
    import time
    acc, m, n = _setup(tmp_path)
    u = acc.sign_up("a@b.in", "longpassword1", "A", "1.1.1.1", terms="x")
    acc.verify(u["id"])                                          # confirmed: 50 free credits
    st = free_status(acc, u["id"])
    assert st["left"] == 50 and abs(st["ends"] - (time.time() + FREE_DAYS * DAY)) < 60
    now = time.time()
    n.run(now + 3 * DAY)
    assert len(m.sent) == 1 and "50 free" in m.sent[0][1]
    acc.charge(acc.user(u["id"]), 10, "export", "Export")
    n.run(now + 6 * DAY)
    assert len(m.sent) == 1                                      # used credits: no more reminders
    acc.add(u["id"], 500, "purchase", "Bought")
    _age(acc, u["id"], 16)
    n.run(now + 30 * DAY)
    assert acc.balance(u["id"]) == 500                           # 40 unused free credits ended, bought ones stay
    n.run(now + 31 * DAY)
    assert acc.balance(u["id"]) == 500 and free_status(acc, u["id"]) is None


def test_reminder_wording():
    s, text, _ = mail.use_reminder("https://x", 50, "29 October 2026", False)
    assert "29 October 2026" in s and "50 free credits" in text
    s, _, _ = mail.use_reminder("https://x", 20, "29 October 2026", True)
    assert s == "Your 20 free credits end tomorrow"
