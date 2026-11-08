"""Who may read which file. Postgres is the only source of truth; nothing here is cached or copied into
Elasticsearch, so a rule change applies to the very next request with no re-index.

Model: an `AccessRule` sits on a folder path or a file path ("" is the whole project) and says `everyone`,
`people` (a list of user ids) or `admin`. A file is governed by the most specific rule that contains it
(the longest path); a file no rule covers is admin only. Admins read everything. Every surface that returns
content or file names goes through `readable_files` / `can_read_file` / `visible_project_ids`."""
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessRule, File, ProjectMember, User

EVERYONE, PEOPLE, ADMIN_ONLY = "everyone", "people", "admin"
VISIBILITIES = (EVERYONE, PEOPLE, ADMIN_ONLY)


# The viewer of offline tools (the retrieval eval, scripts): not a person, reads everything. Application
# code never uses it; request handlers always pass the signed-in user.
SYSTEM_ADMIN = User(id=uuid.UUID(int=0), email="system@localhost", name="system", password_hash="", role="admin")


@dataclass(frozen=True)
class FileRef:
    id: uuid.UUID
    path: str
    status: str


def governing_rule(rules: dict[str, AccessRule], file_path: str) -> AccessRule | None:
    """The most specific rule covering `file_path`: the file's own, then each parent folder, then the root."""
    parts = file_path.split("/")
    for end in range(len(parts), 0, -1):
        rule = rules.get("/".join(parts[:end]))
        if rule is not None:
            return rule
    return rules.get("")


def rule_allows(user: User, rule: AccessRule | None) -> bool:
    if user.is_admin:
        return True
    if rule is None or rule.visibility == ADMIN_ONLY:
        return False
    if rule.visibility == EVERYONE:
        return True
    return str(user.id) in (rule.user_ids or [])


def explain(user: User, rules: dict[str, AccessRule], file_path: str, is_member: bool = True) -> tuple[bool, str]:
    """(may read, why) for the By-person view of the Access page."""
    if user.is_admin:
        return True, "Admins read everything"
    if not is_member:
        return False, "Not a member of this project"
    rule = governing_rule(rules, file_path)
    if rule is None:
        return False, "No rule covers this file, so only admins can read it"
    where = "the whole project" if rule.path == "" else rule.path
    if rule.visibility == EVERYONE:
        return True, f"Everyone can read {where}"
    if rule.visibility == ADMIN_ONLY:
        return False, f"{where} is set to admins only"
    if str(user.id) in (rule.user_ids or []):
        return True, f"Named on the rule for {where}"
    return False, f"Not named on the rule for {where}"


async def load_rules(session: AsyncSession, project_id: uuid.UUID) -> dict[str, AccessRule]:
    rows = (await session.scalars(select(AccessRule).where(AccessRule.project_id == project_id))).all()
    return {r.path: r for r in rows}


async def is_member(session: AsyncSession, user: User, project_id: uuid.UUID) -> bool:
    return bool(await session.scalar(
        select(ProjectMember.id).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
    ))


async def member_ids(session: AsyncSession, project_id: uuid.UUID) -> set[str]:
    return {str(u) for u in (await session.scalars(
        select(ProjectMember.user_id).where(ProjectMember.project_id == project_id)
    )).all()}


async def readable_files(
    session: AsyncSession, user: User, project_id: uuid.UUID, *, ready_only: bool = False,
) -> list[FileRef]:
    """The project's files this user may read, ordered by path."""
    query = select(File.id, File.file_path, File.status).where(File.project_id == project_id)
    if ready_only:
        query = query.where(File.status == "ready")
    rows = (await session.execute(query.order_by(File.file_path))).all()
    if user.is_admin:
        return [FileRef(i, p, s) for i, p, s in rows]
    if not await is_member(session, user, project_id):  # not in this project: nothing in it is readable
        return []
    rules = await load_rules(session, project_id)
    return [FileRef(i, p, s) for i, p, s in rows if rule_allows(user, governing_rule(rules, p))]


async def readable_file_ids(
    session: AsyncSession, user: User, project_id: uuid.UUID, *, ready_only: bool = False,
) -> list[uuid.UUID]:
    return [f.id for f in await readable_files(session, user, project_id, ready_only=ready_only)]


async def can_read_file(session: AsyncSession, user: User, file: File) -> bool:
    if user.is_admin:
        return True
    if not await is_member(session, user, file.project_id):
        return False
    return rule_allows(user, governing_rule(await load_rules(session, file.project_id), file.file_path))


async def can_read_any(session: AsyncSession, user: User, project_id: uuid.UUID) -> bool:
    return bool(await readable_files(session, user, project_id))


async def visible_project_ids(session: AsyncSession, user: User, candidates: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """Projects shown to this user: an admin sees all; a member sees those where at least one file is readable."""
    ids = list(candidates)
    if user.is_admin:
        return set(ids)
    out: set[uuid.UUID] = set()
    for project_id in ids:
        if await can_read_any(session, user, project_id):
            out.add(project_id)
    return out
