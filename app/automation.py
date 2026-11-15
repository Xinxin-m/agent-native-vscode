"""Deterministic provenance for known unattended jobs, independent of titles."""
import copy
import re
from session_lineage import effective_assignments

AUTOMATION_PROJECT = {'id': 'automation-runs', 'name': 'Automation runs', 'archived': True, 'automation': True}
PREFIXES = ()  # every prefix is user-specific, so all of them live in data/config.json


def human_project_name(name, misc):
    return misc if re.sub(r'[^a-z0-9]', '', name.lower()) == 'automationruns' else name


def automation_kind(chat):
    prompt = (chat.get('first_prompt') or '').lstrip()
    if not prompt and chat.get('turns'):
        prompt = next((t.get('text', '').lstrip() for t in chat['turns'] if t.get('role') == 'user'), '')
    return next((kind for prefix, kind in PREFIXES if prompt.startswith(prefix)), None)


def effective_projects(index, projects):
    result = copy.deepcopy(projects)
    runs = [key for key, chat in index.items() if not chat.get('is_child') and automation_kind(chat)]
    if runs:
        project = next((p for p in result['projects'] if p['id'] == AUTOMATION_PROJECT['id']), None)
        if project is None:
            result['projects'].append(dict(AUTOMATION_PROJECT))
        else:
            project.update(AUTOMATION_PROJECT)
        result.setdefault('assignments', {}).update({key: AUTOMATION_PROJECT['id'] for key in runs})
    result['assignments'] = effective_assignments(index, result.get('assignments') or {})
    return result
