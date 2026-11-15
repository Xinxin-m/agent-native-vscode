"""Seed project navigation from recorded workspaces, without provider calls."""
import hashlib
import json
import os
from pathlib import PurePosixPath

from automation import effective_projects

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')


def mark_manual(projects, keys):
    """Remember explicit choices, including choosing the same group or no group."""
    manual = set(projects.get('manual_assignments', []))
    manual.update(keys)
    projects['manual_assignments'] = sorted(manual)
    for key in keys:
        projects.setdefault('workspace_seeds', {}).pop(key, None)


def preserve_project(projects, pid):
    for project in projects.get('projects', []):
        if project['id'] == pid:
            project['user_curated'] = True
    mark_manual(projects, [key for key, value in projects.get('assignments', {}).items() if value == pid])


def refine_workspace_seeds(projects):
    manual = set(projects.get('manual_assignments', []))
    curated = {p['id'] for p in projects.get('projects', []) if p.get('user_curated')}
    for key, pid in projects.pop('workspace_seeds', {}).items():
        if key not in manual and pid not in curated and projects['assignments'].get(key) == pid:
            projects['assignments'].pop(key)


def seed_projects(index, projects):
    result = effective_projects(index, projects)
    assignments = result.setdefault('assignments', {})
    seeds = result.setdefault('workspace_seeds', {})
    by_id = {p['id']: p for p in result['projects']}
    for key, chat in sorted(index.items()):
        if key in assignments or key in result.get('manual_assignments', []) or chat.get('is_child'):
            continue
        cwd = str(chat.get('cwd') or '').strip().replace('\\', '/')
        if not cwd or cwd == '/':
            continue
        cwd = str(PurePosixPath(cwd))
        # Full path identity keeps same-named folders separate. Provider is
        # deliberately absent: Claude and Codex share a workspace project.
        host = str(chat.get('node') or 'local')
        pid = 'workspace-' + hashlib.sha256((host + '\0' + cwd).encode()).hexdigest()[:16]
        if pid not in by_id:
            name = PurePosixPath(cwd).name or 'Workspace'
            project = {'id': pid, 'name': name, 'workspace': cwd,
                       'grouping_source': 'workspace', 'workspace_host': host}
            result['projects'].append(project)
            by_id[pid] = project
        assignments[key] = pid
        if not by_id[pid].get('user_curated'):
            seeds[key] = pid
    return effective_projects(index, result)


def main():
    def load(name, default):
        try:
            with open(os.path.join(DATA, name + '.json')) as fh:
                return json.load(fh)
        except FileNotFoundError:
            return default
    result = seed_projects(load('index', {}), load('projects', {'projects': [], 'assignments': {}}))
    path = os.path.join(DATA, 'projects.json')
    with open(path + '.tmp', 'w') as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(path + '.tmp', path)


if __name__ == '__main__':
    main()
