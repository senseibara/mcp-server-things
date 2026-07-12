"""Heading (section) operations via the Things URL scheme.

Things 3's AppleScript dictionary does NOT expose headings at all — there is
no `heading` class, so any script containing `heading id ...`,
`make new heading`, or `set heading of ...` fails to compile with errors like
"The variable heading is not defined". Cultured Code's documentation is
explicit that heading manipulation is only available through the URL scheme
(and Shortcuts), not AppleScript.

This module therefore implements all heading writes via the URL scheme:

- Creating a heading: `things:///json` with a project `update` operation whose
  attributes contain an `items` array holding the new heading. Requires the
  Things URL-scheme auth token.
- Assigning/moving a todo to a heading: `things:///update` with `heading-id`
  (or `heading` + `list-id`). Requires the auth token.
- Renaming a heading: attempted via `things:///update?id=<heading-id>&title=…`
  (headings are tasks internally); verified by reading the database back. If
  Things ignores the command, a clear "not supported" error is returned.
- Deleting a heading: not supported by any public Things API. A clear error
  with guidance is returned instead of a cryptic AppleScript failure.

Because `open -g 'things:///…'` returns before Things has processed the
command, every write here is verified by polling the Things database through
things.py until the change is visible (or a timeout is reached).
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import things

logger = logging.getLogger(__name__)

# How long to poll the Things database waiting for a URL-scheme command to
# take effect. Things usually processes URLs within a few hundred ms.
_VERIFY_ATTEMPTS = 12
_VERIFY_DELAY_SECONDS = 0.25

AUTH_TOKEN_HELP = (
    "A Things URL-scheme auth token is required for heading operations. "
    "Get it from Things → Settings → General → Enable Things URLs → Manage, "
    "then save it to a file named '.things-auth' in the project root or your "
    "home directory."
)

NOT_SUPPORTED_DELETE = (
    "Things 3 provides no public API (AppleScript, URL scheme, or JSON "
    "command) to delete a heading. Delete it in the Things app directly "
    "(right-click the heading → Delete), or move its todos elsewhere first "
    "using move_record/update_todo with heading_id."
)


class HeadingOperations:
    """URL-scheme-backed heading operations with database verification."""

    def __init__(self, applescript_manager):
        """
        Args:
            applescript_manager: AppleScriptManager instance; provides
                `execute_url_scheme` and the loaded `auth_token`.
        """
        self.applescript = applescript_manager

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_auth_token(self) -> Optional[Dict[str, Any]]:
        """Return an error dict if no auth token is configured, else None."""
        if not getattr(self.applescript, "auth_token", None):
            return {
                "success": False,
                "error": "AUTH_TOKEN_MISSING",
                "message": AUTH_TOKEN_HELP,
            }
        return None

    @staticmethod
    def _get_project_headings(project_id: str) -> List[Dict[str, Any]]:
        """Read a project's headings straight from the Things database."""
        try:
            return list(things.tasks(type="heading", project=project_id) or [])
        except Exception as e:  # pragma: no cover - db access issues
            logger.error(f"Error reading headings for project {project_id}: {e}")
            return []

    @staticmethod
    def _get_task(task_id: str) -> Optional[Dict[str, Any]]:
        """Read a single task/heading from the Things database by ID."""
        try:
            return things.get(task_id)
        except Exception as e:  # pragma: no cover
            logger.error(f"Error reading task {task_id}: {e}")
            return None

    async def _poll(self, predicate) -> Optional[Any]:
        """Poll a synchronous predicate until it returns a truthy value.

        The predicate runs in a thread executor so the database reads don't
        block the event loop. Returns the predicate's value, or None on
        timeout.
        """
        loop = asyncio.get_event_loop()
        for _ in range(_VERIFY_ATTEMPTS):
            await asyncio.sleep(_VERIFY_DELAY_SECONDS)
            result = await loop.run_in_executor(None, predicate)
            if result:
                return result
        return None

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    async def create_heading(self, title: str, project_id: str) -> Dict[str, Any]:
        """Create a heading inside an existing project.

        Uses the `json` URL command with a project `update` operation that
        appends an `items` array containing the heading. The new heading's ID
        is recovered by diffing the project's headings before and after.
        """
        auth_error = self._require_auth_token()
        if auth_error:
            return auth_error

        loop = asyncio.get_event_loop()

        # Confirm the target project exists and snapshot current heading IDs
        # so we can identify the newly created one even with duplicate titles.
        project = await loop.run_in_executor(None, self._get_task, project_id)
        if not project or project.get("type") != "project":
            return {
                "success": False,
                "error": "PROJECT_NOT_FOUND",
                "message": (
                    f"No project with ID '{project_id}' found. Headings can "
                    "only be created inside projects (not areas)."
                ),
            }

        before_ids = {
            h.get("uuid")
            for h in await loop.run_in_executor(
                None, self._get_project_headings, project_id
            )
        }

        payload = [
            {
                "type": "project",
                "operation": "update",
                "id": project_id,
                "attributes": {
                    "items": [
                        {"type": "heading", "attributes": {"title": title}}
                    ]
                },
            }
        ]
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

        result = await self.applescript.execute_url_scheme("json", {"data": data})
        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error", "URL scheme execution failed"),
                "message": "Failed to create heading",
            }

        def find_new_heading():
            for h in self._get_project_headings(project_id):
                if h.get("uuid") not in before_ids and h.get("title") == title:
                    return h
            return None

        new_heading = await self._poll(find_new_heading)
        if new_heading:
            return {
                "success": True,
                "heading_id": new_heading.get("uuid"),
                "title": title,
                "project_id": project_id,
                "message": "Heading created successfully",
            }

        return {
            "success": False,
            "error": "VERIFICATION_FAILED",
            "message": (
                "The create-heading command was sent to Things but the new "
                "heading did not appear in the database. Check that the URL "
                "scheme is enabled and the auth token is valid "
                "(Things → Settings → General)."
            ),
        }

    async def assign_todo_to_heading(
        self,
        todo_id: str,
        heading_id: str = "",
        heading: str = "",
        project_id: str = "",
    ) -> Dict[str, Any]:
        """Move an existing todo under a heading via `things:///update`.

        The URL scheme requires the todo to end up in the project containing
        the heading, so when only `heading_id` is given the enclosing project
        is looked up from the database and passed as `list-id` in the same
        call (this also moves the todo into the project if needed).
        """
        auth_error = self._require_auth_token()
        if auth_error:
            return auth_error

        loop = asyncio.get_event_loop()
        params: Dict[str, Any] = {"id": todo_id}

        if heading_id:
            heading_task = await loop.run_in_executor(None, self._get_task, heading_id)
            if not heading_task or heading_task.get("type") != "heading":
                return {
                    "success": False,
                    "error": "HEADING_NOT_FOUND",
                    "message": f"No heading with ID '{heading_id}' found.",
                }
            enclosing_project = heading_task.get("project") or project_id
            if enclosing_project:
                params["list-id"] = enclosing_project
            params["heading-id"] = heading_id
            expected = lambda t: t and t.get("heading") == heading_id
        elif heading:
            if not project_id:
                return {
                    "success": False,
                    "error": "MISSING_PROJECT",
                    "message": (
                        "Assigning by heading title requires the ID of the "
                        "project containing it (list_id), or use heading_id."
                    ),
                }
            params["list-id"] = project_id
            params["heading"] = heading

            def expected(t):
                if not t or not t.get("heading"):
                    return False
                h = self._get_task(t["heading"])
                return bool(h and h.get("title") == heading)
        else:
            return {
                "success": False,
                "error": "MISSING_HEADING",
                "message": "Either heading_id or heading must be provided.",
            }

        result = await self.applescript.execute_url_scheme("update", params)
        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error", "URL scheme execution failed"),
                "message": "Failed to assign todo to heading",
            }

        verified = await self._poll(lambda: expected(self._get_task(todo_id)))
        if verified:
            return {
                "success": True,
                "todo_id": todo_id,
                "message": "Todo assigned to heading successfully",
            }

        return {
            "success": False,
            "error": "VERIFICATION_FAILED",
            "message": (
                "The update command was sent to Things but the todo's heading "
                "did not change. Verify the heading exists in the todo's "
                "project and that the auth token is valid."
            ),
        }

    async def rename_heading(self, heading_id: str, new_title: str) -> Dict[str, Any]:
        """Rename a heading via `things:///update` with verification.

        Headings are tasks internally; the `update` command accepts their IDs
        for title changes. The database is read back to confirm — if Things
        ignored the command, an explicit error is returned.
        """
        auth_error = self._require_auth_token()
        if auth_error:
            return auth_error

        loop = asyncio.get_event_loop()
        heading_task = await loop.run_in_executor(None, self._get_task, heading_id)
        if not heading_task or heading_task.get("type") != "heading":
            return {
                "success": False,
                "error": "HEADING_NOT_FOUND",
                "message": f"No heading with ID '{heading_id}' found.",
            }
        if heading_task.get("title") == new_title:
            return {
                "success": True,
                "heading_id": heading_id,
                "message": "Heading already has this title",
            }

        result = await self.applescript.execute_url_scheme(
            "update", {"id": heading_id, "title": new_title}
        )
        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error", "URL scheme execution failed"),
                "message": "Failed to rename heading",
            }

        verified = await self._poll(
            lambda: (self._get_task(heading_id) or {}).get("title") == new_title
        )
        if verified:
            return {
                "success": True,
                "heading_id": heading_id,
                "title": new_title,
                "message": "Heading renamed successfully",
            }

        return {
            "success": False,
            "error": "NOT_SUPPORTED_OR_FAILED",
            "message": (
                "Things did not apply the title change. Renaming headings via "
                "the URL scheme may not be supported by your Things version; "
                "rename it in the Things app directly."
            ),
        }

    async def delete_heading(self, heading_id: str) -> Dict[str, Any]:
        """Deleting headings is not possible via any public Things API."""
        return {
            "success": False,
            "error": "NOT_SUPPORTED",
            "message": NOT_SUPPORTED_DELETE,
        }
