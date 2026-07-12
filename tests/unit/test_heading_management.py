"""Test heading (section) CRUD operations and heading assignment on todos.

Heading writes CANNOT use AppleScript: Things 3's scripting dictionary has no
`heading` class ("The variable heading is not defined" errors). The correct
channel is the Things URL scheme, so these tests assert that:

- add_heading issues a `json` URL command (project update + items) and
  recovers the new heading's ID from the database,
- add_todo/update_todo assign headings via the `update` URL command,
- move_record with a `heading:` destination uses the URL scheme,
- update_heading renames via the URL scheme with database verification,
- delete_heading returns a clear "not supported" error.

Database reads (things.py) and URL execution are mocked; verification polling
delays are zeroed for test speed.
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from things_mcp.tools import ThingsTools
from things_mcp.scheduling import heading_operations


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    """Make database verification polling instantaneous in tests."""
    monkeypatch.setattr(heading_operations, "_VERIFY_DELAY_SECONDS", 0)
    monkeypatch.setattr(heading_operations, "_VERIFY_ATTEMPTS", 3)


@pytest.fixture
def mock_applescript():
    """Create a mock AppleScript manager with URL-scheme support."""
    mock = MagicMock()
    mock.auth_token = "test-auth-token"
    mock.execute_applescript = AsyncMock(return_value={
        'success': True,
        'output': 'TODO-ID-1'
    })
    mock.execute_url_scheme = AsyncMock(return_value={
        'success': True,
        'url': 'things:///mock',
        'message': 'ok'
    })
    return mock


@pytest.fixture
def tools(mock_applescript):
    """Create ThingsTools with mocked AppleScript manager."""
    return ThingsTools(mock_applescript)


def make_things_mock(*, headings_before=None, headings_after=None, tasks_by_id=None):
    """Build a mock for the `things` module used by heading_operations.

    Args:
        headings_before: list returned by the first things.tasks() call
            (the pre-create snapshot); defaults to empty
        headings_after: list returned by subsequent things.tasks() calls
            (post-create verification reads)
        tasks_by_id: dict mapping task/heading IDs to task dicts for
            things.get()
    """
    things_mock = MagicMock()
    before = headings_before or []
    after = headings_after if headings_after is not None else before
    state = {'calls': 0}

    def tasks_side_effect(*args, **kwargs):
        state['calls'] += 1
        return before if state['calls'] == 1 else after

    things_mock.tasks.side_effect = tasks_side_effect
    lookup = tasks_by_id or {}
    things_mock.get.side_effect = lambda task_id: lookup.get(task_id)
    return things_mock


PROJECT = {'uuid': 'PROJECT-ID-1', 'type': 'project', 'title': 'My Project'}
NEW_HEADING = {'uuid': 'HEADING-ID-123', 'type': 'heading',
               'title': 'Research', 'project': 'PROJECT-ID-1'}


@pytest.mark.asyncio
class TestAddHeading:
    """add_heading must use the URL scheme json command, never AppleScript."""

    async def test_add_heading_succeeds(self, tools, mock_applescript):
        things_mock = make_things_mock(
            headings_after=[NEW_HEADING],
            tasks_by_id={'PROJECT-ID-1': PROJECT},
        )
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.add_heading(title="Research", list_id="PROJECT-ID-1")

        assert result['success'] is True
        assert result['heading_id'] == 'HEADING-ID-123'

        # Must NOT have gone through AppleScript (no heading class exists)
        mock_applescript.execute_applescript.assert_not_called()

        # Must have used the json URL command with a project update op
        mock_applescript.execute_url_scheme.assert_called_once()
        action, params = mock_applescript.execute_url_scheme.call_args[0]
        assert action == 'json'
        payload = json.loads(params['data'])
        assert payload[0]['type'] == 'project'
        assert payload[0]['operation'] == 'update'
        assert payload[0]['id'] == 'PROJECT-ID-1'
        items = payload[0]['attributes']['items']
        assert items == [{'type': 'heading', 'attributes': {'title': 'Research'}}]

    async def test_add_heading_requires_project(self, tools):
        result = await tools.add_heading(title="Research")
        assert result['success'] is False

    async def test_add_heading_requires_auth_token(self, tools, mock_applescript):
        mock_applescript.auth_token = None
        things_mock = make_things_mock(tasks_by_id={'PROJECT-ID-1': PROJECT})
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.add_heading(title="Research", list_id="PROJECT-ID-1")

        assert result['success'] is False
        assert 'auth' in result['message'].lower()
        mock_applescript.execute_url_scheme.assert_not_called()

    async def test_add_heading_rejects_unknown_project(self, tools, mock_applescript):
        things_mock = make_things_mock(tasks_by_id={})
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.add_heading(title="Research", list_id="NOPE")

        assert result['success'] is False
        assert result['error'] == 'PROJECT_NOT_FOUND'

    async def test_add_heading_reports_verification_failure(self, tools, mock_applescript):
        # URL fires fine, but the heading never appears in the database
        things_mock = make_things_mock(
            headings_after=[],
            tasks_by_id={'PROJECT-ID-1': PROJECT},
        )
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.add_heading(title="Research", list_id="PROJECT-ID-1")

        assert result['success'] is False
        assert result['error'] == 'VERIFICATION_FAILED'


@pytest.mark.asyncio
class TestUpdateHeading:
    """Renaming goes through the URL scheme with read-back verification."""

    async def test_update_with_none_fails(self, tools):
        result = await tools.update_heading(None, title="Renamed")
        assert result['success'] is False
        assert result.get('field') == 'heading_id'

    async def test_update_with_empty_string_fails(self, tools):
        result = await tools.update_heading('', title="Renamed")
        assert result['success'] is False
        assert result.get('field') == 'heading_id'

    async def test_update_requires_title(self, tools):
        result = await tools.update_heading('HEADING-ID-123')
        assert result['success'] is False

    async def test_update_with_valid_id_succeeds(self, tools, mock_applescript):
        renamed = dict(NEW_HEADING, title='Renamed Heading')
        # First read returns the old title, verification reads the new one
        things_mock = MagicMock()
        things_mock.get.side_effect = [NEW_HEADING, renamed]
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.update_heading('HEADING-ID-123', title="Renamed Heading")

        assert result['success'] is True
        mock_applescript.execute_applescript.assert_not_called()
        action, params = mock_applescript.execute_url_scheme.call_args[0]
        assert action == 'update'
        assert params == {'id': 'HEADING-ID-123', 'title': 'Renamed Heading'}

    async def test_update_unknown_heading_fails(self, tools, mock_applescript):
        things_mock = make_things_mock(tasks_by_id={})
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.update_heading('NonexistentID', title="X")

        assert result['success'] is False
        assert result['error'] == 'HEADING_NOT_FOUND'

    async def test_update_reports_when_things_ignores_command(self, tools, mock_applescript):
        # Title never changes in the database -> explicit failure, not silence
        things_mock = make_things_mock(tasks_by_id={'HEADING-ID-123': NEW_HEADING})
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.update_heading('HEADING-ID-123', title="Renamed")

        assert result['success'] is False
        assert 'rename' in result['message'].lower() or 'title' in result['message'].lower()


@pytest.mark.asyncio
class TestDeleteHeading:
    """Deletion is impossible via public APIs; the error must say so."""

    async def test_delete_with_none_fails(self, tools):
        result = await tools.delete_heading(None)
        assert result['success'] is False
        assert result.get('field') == 'heading_id'

    async def test_delete_returns_clear_not_supported_error(self, tools, mock_applescript):
        result = await tools.delete_heading('HEADING-ID-123')

        assert result['success'] is False
        assert result['error'] == 'NOT_SUPPORTED'
        # Guidance, not a cryptic AppleScript error
        assert 'Things app' in result['message']
        mock_applescript.execute_applescript.assert_not_called()
        mock_applescript.execute_url_scheme.assert_not_called()


@pytest.mark.asyncio
class TestAddTodoHeadingAssignment:
    """add_todo creates via AppleScript, then assigns the heading via the
    URL scheme update command (with list-id resolved from the database)."""

    async def test_add_todo_with_heading_id(self, tools, mock_applescript):
        todo_in_heading = {'uuid': 'TODO-ID-1', 'type': 'to-do',
                           'heading': 'HEADING-ID-123'}
        things_mock = make_things_mock(tasks_by_id={
            'HEADING-ID-123': NEW_HEADING,
            'TODO-ID-1': todo_in_heading,
        })
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.add_todo(
                title="Research competitors",
                heading_id="HEADING-ID-9" if False else "HEADING-ID-123",
            )

        assert result['success'] is True
        assert result['todo_id'] == 'TODO-ID-1'
        assert 'warning' not in result

        # Creation script must not mention headings at all
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'heading' not in script.lower()

        # Assignment via URL scheme, including the enclosing project
        action, params = mock_applescript.execute_url_scheme.call_args[0]
        assert action == 'update'
        assert params['id'] == 'TODO-ID-1'
        assert params['heading-id'] == 'HEADING-ID-123'
        assert params['list-id'] == 'PROJECT-ID-1'

    async def test_add_todo_with_heading_title_and_project(self, tools, mock_applescript):
        todo_in_heading = {'uuid': 'TODO-ID-1', 'type': 'to-do',
                           'heading': 'HEADING-ID-123'}
        things_mock = make_things_mock(tasks_by_id={
            'HEADING-ID-123': NEW_HEADING,
            'TODO-ID-1': todo_in_heading,
        })
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.add_todo(
                title="Research competitors",
                list_id="PROJECT-ID-1",
                heading="Research",
            )

        assert result['success'] is True
        action, params = mock_applescript.execute_url_scheme.call_args[0]
        assert action == 'update'
        assert params['heading'] == 'Research'
        assert params['list-id'] == 'PROJECT-ID-1'

    async def test_add_todo_with_heading_but_no_project_fails_clearly(self, tools):
        result = await tools.add_todo(title="Research competitors", heading="Research")
        assert result['success'] is False
        assert 'heading' in result['error'].lower()

    async def test_add_todo_without_heading_makes_no_url_call(self, tools, mock_applescript):
        result = await tools.add_todo(title="Plain todo", list_id="PROJECT-ID-1")

        assert result['success'] is True
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'heading' not in script.lower()
        mock_applescript.execute_url_scheme.assert_not_called()

    async def test_add_todo_reports_failed_assignment_as_warning(self, tools, mock_applescript):
        # Heading exists but the todo never lands under it
        things_mock = make_things_mock(tasks_by_id={
            'HEADING-ID-123': NEW_HEADING,
            'TODO-ID-1': {'uuid': 'TODO-ID-1', 'type': 'to-do', 'heading': None},
        })
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.add_todo(
                title="Research competitors",
                heading_id="HEADING-ID-123",
            )

        # Todo creation itself succeeded, but the caller must be told the
        # heading assignment did not stick.
        assert result['success'] is True
        assert 'warning' in result
        assert 'heading' in result['warning'].lower()


@pytest.mark.asyncio
class TestUpdateTodoHeadingAssignment:
    """update_todo moves todos under headings via the URL scheme."""

    async def test_update_todo_with_heading_id(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True, 'output': 'updated'
        }
        todo_in_heading = {'uuid': 'TODO-ID-1', 'type': 'to-do',
                           'heading': 'HEADING-ID-123'}
        things_mock = make_things_mock(tasks_by_id={
            'HEADING-ID-123': NEW_HEADING,
            'TODO-ID-1': todo_in_heading,
        })
        with patch.object(heading_operations, 'things', things_mock):
            result = await tools.update_todo(
                todo_id='TODO-ID-1',
                heading_id='HEADING-ID-123',
            )

        assert result['success'] is True
        assert 'warning' not in result
        action, params = mock_applescript.execute_url_scheme.call_args[0]
        assert action == 'update'
        assert params['id'] == 'TODO-ID-1'
        assert params['heading-id'] == 'HEADING-ID-123'

    async def test_update_todo_heading_title_requires_project(self, tools):
        result = await tools.update_todo(todo_id='TODO-ID-1', heading='Research')
        assert result['success'] is False
        assert 'heading' in result['error'].lower()


@pytest.mark.asyncio
class TestMoveRecordToHeading:
    """move_record with a heading: destination uses the URL scheme."""

    async def test_move_to_heading_uses_url_scheme(self, mock_applescript):
        from things_mcp.move_operations import MoveOperationsTools

        move_tools = MoveOperationsTools(mock_applescript, MagicMock())
        todo_in_heading = {'uuid': 'TODO-ID-1', 'type': 'to-do',
                           'heading': 'HEADING-ID-123'}
        things_mock = make_things_mock(tasks_by_id={
            'HEADING-ID-123': NEW_HEADING,
            'TODO-ID-1': todo_in_heading,
        })
        with patch.object(heading_operations, 'things', things_mock):
            result = await move_tools._execute_move(
                'TODO-ID-1', 'heading:HEADING-ID-123', {'title': 'Todo'}
            )

        assert result['success'] is True
        mock_applescript.execute_applescript.assert_not_called()
        action, params = mock_applescript.execute_url_scheme.call_args[0]
        assert action == 'update'
        assert params['heading-id'] == 'HEADING-ID-123'
        assert params['list-id'] == 'PROJECT-ID-1'

    async def test_move_to_unknown_heading_fails_clearly(self, mock_applescript):
        from things_mcp.move_operations import MoveOperationsTools

        move_tools = MoveOperationsTools(mock_applescript, MagicMock())
        things_mock = make_things_mock(tasks_by_id={})
        with patch.object(heading_operations, 'things', things_mock):
            result = await move_tools._execute_move(
                'TODO-ID-1', 'heading:NOPE', {'title': 'Todo'}
            )

        assert result['success'] is False
        assert result['error'] == 'HEADING_NOT_FOUND'
