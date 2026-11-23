"""Database: tasks, steps, interactions (approvals and questions) and learned skills.

Works on SQLite (local) and PostgreSQL (production). On PostgreSQL with the pgvector
extension the skill embeddings use a real `vector` column; elsewhere they are JSON.
"""
from __future__ import annotations

import json
import math
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, event, select,
                        text, update)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from webagent.config import settings

EMBED_DIM = 256
ACTIVE = ("queued", "running", "waiting_approval", "waiting_user")
FINAL = ("done", "failed", "cancelled")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _vector_type():
    if settings.is_postgres:
        try:
            from pgvector.sqlalchemy import Vector

            return Vector(EMBED_DIM)
        except ImportError:  # pragma: no cover
            pass
    return JSON


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    request: Mapped[str] = mapped_column(Text)
    start_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    plan: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_count: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    current_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Step(Base):
    __tablename__ = "steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    n: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(20))
    tool: Mapped[str | None] = mapped_column(String(40), nullable=True)
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Interaction(Base):
    """A question to the person: approve a risky action, or answer the agent."""
    __tablename__ = "interactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # approval | question
    message: Mapped[str] = mapped_column(Text)
    action: Mapped[dict] = mapped_column(JSON, default=dict)
    answer: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # True: no worker holds the task while it waits; the answer must start it again (see runner.PARKED).
    parked: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)


class Skill(Base):
    """Something the agent learned about a website ("search box is at the top, results show price under title")."""
    __tablename__ = "skills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(200), index=True)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(_vector_type(), nullable=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Watch(Base):
    """A task that runs again on a schedule (monitoring), and notices when its answer changes."""
    __tablename__ = "watches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex[:10])
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    request: Mapped[str] = mapped_column(Text)
    every_minutes: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    last_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    changed: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------- engine

def _make_engine(url: str):
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(eng, "connect")
        def _pragmas(conn, _):  # WAL: the API reads while the worker writes
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
_ready = False


def init_db() -> None:
    global _ready
    if _ready:
        return
    if settings.is_postgres:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _ready = True


def _add_missing_columns() -> None:
    """Tiny migration: add columns that newer versions added to existing tables (nullable only)."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have and col.nullable:
                ddl = col.type.compile(dialect=engine.dialect)
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))


@contextmanager
def session() -> Iterator[Session]:
    init_db()
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ---------------------------------------------------------------- tasks

def task_dict(t: Task) -> dict:
    return {
        "id": t.id, "conversation_id": t.conversation_id, "request": t.request, "start_url": t.start_url,
        "status": t.status, "answer": t.answer, "sources": t.sources or [], "plan": t.plan or [],
        "error": t.error, "steps_count": t.steps_count, "tokens": t.tokens, "current_url": t.current_url,
        "watch_id": t.watch_id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
    }


def create_task(request: str, conversation_id: str | None = None, start_url: str | None = None,
                watch_id: str | None = None) -> dict:
    with session() as s:
        t = Task(request=request, conversation_id=conversation_id or uuid.uuid4().hex[:12], start_url=start_url,
                 sources=[], plan=[], watch_id=watch_id)
        s.add(t)
        s.flush()
        return task_dict(t)


def get_task(task_id: str) -> dict | None:
    with session() as s:
        t = s.get(Task, task_id)
        return task_dict(t) if t else None


def list_tasks(conversation_id: str | None = None, limit: int = 50) -> list[dict]:
    with session() as s:
        q = select(Task).order_by(Task.created_at.desc()).limit(limit)
        if conversation_id:
            q = q.where(Task.conversation_id == conversation_id)
        return [task_dict(t) for t in s.scalars(q)]


def update_task(task_id: str, **fields) -> None:
    if fields.get("status") in FINAL:
        fields.setdefault("finished_at", now())
    fields["updated_at"] = now()
    with session() as s:
        s.execute(update(Task).where(Task.id == task_id).values(**fields))


def add_tokens(task_id: str, n: int) -> None:
    if n:
        with session() as s:
            s.execute(update(Task).where(Task.id == task_id).values(tokens=Task.tokens + n))


def cancel_requested(task_id: str) -> bool:
    with session() as s:
        t = s.get(Task, task_id)
        return bool(t and t.cancel_requested)


def conversation_history(conversation_id: str, before_task: str, limit: int = 5) -> list[dict]:
    """Earlier finished tasks in the same chat, oldest first (for follow-up questions)."""
    with session() as s:
        q = (select(Task).where(Task.conversation_id == conversation_id, Task.id != before_task,
                                Task.status == "done").order_by(Task.created_at.desc()).limit(limit))
        rows = [task_dict(t) for t in s.scalars(q)]
    return list(reversed(rows))


def recover_stale_tasks() -> int:
    """Tasks left 'running' by a crashed worker are marked failed at start-up (one worker per browser)."""
    with session() as s:
        res = s.execute(update(Task).where(Task.status.in_(("running", "waiting_approval", "waiting_user")))
                        .values(status="failed", error="The worker stopped while this task was running.",
                                finished_at=now(), updated_at=now()))
        return res.rowcount or 0


# ---------------------------------------------------------------- steps

def add_step(task_id: str, node: str, tool: str | None = None, args: dict | None = None, why: str | None = None,
             result: str | None = None, ok: bool = True, url: str | None = None) -> int:
    with session() as s:
        t = s.get(Task, task_id)
        n = (t.steps_count if t else 0) + 1
        s.add(Step(task_id=task_id, n=n, node=node, tool=tool, args=args or {}, why=why,
                   result=(result or "")[:4000], ok=ok, url=url))
        if t:
            t.steps_count = n
            t.updated_at = now()
            if url:
                t.current_url = url
        return n


def get_steps(task_id: str) -> list[dict]:
    with session() as s:
        q = select(Step).where(Step.task_id == task_id).order_by(Step.n)
        return [{"n": r.n, "node": r.node, "tool": r.tool, "args": r.args, "why": r.why, "result": r.result,
                 "ok": r.ok, "url": r.url, "at": r.created_at.isoformat()} for r in s.scalars(q)]


# ---------------------------------------------------------------- interactions

def open_interaction(task_id: str, kind: str, message: str, action: dict | None = None) -> int:
    with session() as s:
        i = Interaction(task_id=task_id, kind=kind, message=message, action=action or {})
        s.add(i)
        s.flush()
        return i.id


def pending_interaction(task_id: str) -> dict | None:
    with session() as s:
        q = (select(Interaction).where(Interaction.task_id == task_id, Interaction.decided_at.is_(None))
             .order_by(Interaction.id.desc()).limit(1))
        i = s.scalars(q).first()
        if not i:
            return None
        return {"id": i.id, "kind": i.kind, "message": i.message, "action": i.action,
                "created_at": i.created_at.isoformat()}


def answer_interaction(task_id: str, answer: dict, kind: str | None = None) -> bool:
    with session() as s:
        q = select(Interaction).where(Interaction.task_id == task_id, Interaction.decided_at.is_(None))
        if kind:
            q = q.where(Interaction.kind == kind)
        i = s.scalars(q.order_by(Interaction.id.desc()).limit(1)).first()
        if not i:
            return False
        i.answer = answer
        i.decided_at = now()
        return True


def park_interaction(interaction_id: int) -> bool:
    """Mark an open question/approval as parked. False if the person answered in the meantime."""
    from sqlalchemy import update
    with session() as s:
        r = s.execute(update(Interaction).where(Interaction.id == interaction_id, Interaction.decided_at.is_(None))
                      .values(parked=True))
        return r.rowcount == 1


def last_interaction(task_id: str) -> dict | None:
    with session() as s:
        i = s.scalars(select(Interaction).where(Interaction.task_id == task_id)
                      .order_by(Interaction.id.desc()).limit(1)).first()
        if not i:
            return None
        return {"id": i.id, "kind": i.kind, "action": i.action or {}, "answer": i.answer, "parked": bool(i.parked),
                "created_at": i.created_at.isoformat(),
                "decided_at": i.decided_at.isoformat() if i.decided_at else None}


def interaction_answer(interaction_id: int) -> dict | None:
    with session() as s:
        i = s.get(Interaction, interaction_id)
        return i.answer if i and i.decided_at else None


# ---------------------------------------------------------------- skills

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def save_skill(domain: str, text_: str, embedding: list[float]) -> None:
    domain = domain.lower()
    with session() as s:
        # Replace a near-duplicate instead of piling up copies of the same lesson.
        for sk in s.scalars(select(Skill).where(Skill.domain == domain)):
            emb = list(sk.embedding) if sk.embedding is not None else None
            if emb and _cosine(emb, embedding) > 0.92:
                sk.text, sk.embedding, sk.updated_at = text_, embedding, now()
                return
        s.add(Skill(domain=domain, text=text_, embedding=embedding))


def find_skills(domains: list[str], embedding: list[float], limit: int = 6) -> list[dict]:
    """Skills for the given websites first, then the most similar ones from any website."""
    domains = [d.lower() for d in domains]
    out: list[dict] = []
    with session() as s:
        if domains:
            for sk in s.scalars(select(Skill).where(Skill.domain.in_(domains)).order_by(Skill.updated_at.desc())
                                .limit(limit)):
                out.append({"id": sk.id, "domain": sk.domain, "text": sk.text, "score": 1.0})
        rest = limit - len(out)
        if rest > 0:
            seen = {o["id"] for o in out}
            if settings.is_postgres and hasattr(Skill.embedding.type, "dim"):
                q = select(Skill, Skill.embedding.cosine_distance(embedding).label("d")).order_by(text("d")).limit(rest + len(seen))
                for sk, d in s.execute(q):
                    if sk.id not in seen and 1 - d > 0.35:
                        out.append({"id": sk.id, "domain": sk.domain, "text": sk.text, "score": round(1 - d, 3)})
            else:
                scored = []
                for sk in s.scalars(select(Skill).order_by(Skill.updated_at.desc()).limit(500)):
                    if sk.id in seen or sk.embedding is None:
                        continue
                    scored.append((_cosine(list(sk.embedding), embedding), sk))
                for score, sk in sorted(scored, key=lambda x: -x[0])[:rest]:
                    if score > 0.35:
                        out.append({"id": sk.id, "domain": sk.domain, "text": sk.text, "score": round(score, 3)})
        ids = [o["id"] for o in out]
        if ids:
            s.execute(update(Skill).where(Skill.id.in_(ids)).values(uses=Skill.uses + 1))
    return out[:limit]


def list_skills(limit: int = 100) -> list[dict]:
    with session() as s:
        return [{"id": k.id, "domain": k.domain, "text": k.text, "uses": k.uses,
                 "updated_at": k.updated_at.isoformat()} for k in
                s.scalars(select(Skill).order_by(Skill.updated_at.desc()).limit(limit))]


# ---------------------------------------------------------------- watches

def watch_dict(w: Watch) -> dict:
    return {"id": w.id, "conversation_id": w.conversation_id, "request": w.request, "every_minutes": w.every_minutes,
            "active": w.active, "runs": w.runs, "last_task_id": w.last_task_id, "last_answer": w.last_answer,
            "last_status": w.last_status, "changed": w.changed,
            "changed_at": w.changed_at.isoformat() if w.changed_at else None,
            "next_run_at": w.next_run_at.isoformat() if w.next_run_at else None,
            "last_run_at": w.last_run_at.isoformat() if w.last_run_at else None}


def create_watch(request: str, every_minutes: int, conversation_id: str | None = None) -> dict:
    with session() as s:
        w = Watch(request=request, every_minutes=every_minutes, conversation_id=conversation_id or uuid.uuid4().hex[:12],
                  next_run_at=now())
        s.add(w)
        s.flush()
        return watch_dict(w)


def list_watches(active_only: bool = True) -> list[dict]:
    with session() as s:
        q = select(Watch).order_by(Watch.created_at.desc())
        if active_only:
            q = q.where(Watch.active.is_(True))
        return [watch_dict(w) for w in s.scalars(q)]


def get_watch(watch_id: str) -> dict | None:
    with session() as s:
        w = s.get(Watch, watch_id)
        return watch_dict(w) if w else None


def stop_watch(watch_id: str) -> bool:
    with session() as s:
        w = s.get(Watch, watch_id)
        if not w:
            return False
        w.active = False
        return True


def claim_due_watches(limit: int = 10) -> list[dict]:
    """Watches whose time has come; their next run is moved forward so no other scheduler takes them."""
    from datetime import timedelta

    out = []
    with session() as s:
        q = (select(Watch).where(Watch.active.is_(True), Watch.next_run_at <= now()).order_by(Watch.next_run_at)
             .limit(limit))
        if settings.is_postgres:
            q = q.with_for_update(skip_locked=True)
        for w in s.scalars(q):
            # Skip if the previous run is still going.
            if w.last_task_id:
                last = s.get(Task, w.last_task_id)
                if last and last.status in ACTIVE:
                    w.next_run_at = now() + timedelta(minutes=1)
                    continue
            w.next_run_at = now() + timedelta(minutes=w.every_minutes)
            w.last_run_at = now()
            w.runs += 1
            out.append(watch_dict(w))
    return out


def set_watch_task(watch_id: str, task_id: str) -> None:
    with session() as s:
        w = s.get(Watch, watch_id)
        if w:
            w.last_task_id = task_id


def record_watch_result(watch_id: str, status: str, answer: str) -> None:
    with session() as s:
        w = s.get(Watch, watch_id)
        if not w:
            return
        changed = status == "done" and answer.strip().upper().startswith("CHANGED")
        w.last_status = status
        if status == "done":
            w.last_answer = answer
        if changed:
            w.changed, w.changed_at = True, now()


def health() -> str:
    try:
        with session() as s:
            s.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # pragma: no cover
        return f"error: {type(exc).__name__}"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def delete_skills(domain: str) -> int:
    """Forget what was learned about one website (the eval clears its own lab site before a run)."""
    from sqlalchemy import delete
    with session() as s:
        n = s.execute(delete(Skill).where(Skill.domain == domain.lower())).rowcount or 0
        s.commit()
        return n
