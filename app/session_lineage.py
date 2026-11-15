"""Explicit native session lineage and transcript drill-in metadata."""
import json
import os

from indexer import LINEAGE_FIELDS, resolve_lineage

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def _load(name, default):
    try:
        with open(os.path.join(DATA, name + '.json')) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def effective_assignments(index, assignments):
    """Return inherited placement without modifying curated project storage."""
    result = dict(assignments)
    for key, meta in index.items():
        if not meta.get('is_child'):
            continue
        root = meta.get('root_key')
        if root in assignments:
            result[key] = assignments[root]
        else:
            result.pop(key, None)
    return result


def resume_availability(meta):
    """Verify a resumable local native identity without launching a provider."""
    host=meta.get('execution_host') or meta.get('host_id') or meta.get('node') or 'mac'
    if host not in ('mac','local'):
        return {'native_resumable':False,'resume_reason':'Native resume ownership must be verified on its execution host'}
    try:
        import host_connector
        selected=host_connector.resolve_local_account(meta['source'],account_id=meta.get('account_id'),model=meta.get('model_main'),session_id=meta['id'],cwd=os.path.realpath(meta.get('cwd') or ''))
        return {'native_resumable':True,'resume_account_id':selected['account_id']}
    except (ValueError,OSError,KeyError):
        return {'native_resumable':False,'resume_reason':'This transcript has no verified resumable native session; start a linked session from a response instead.'}


def session_tree(req, query, body):
    index = resolve_lineage(_load('index', {}))
    key = (query or {}).get('key') or ''
    if key not in index:
        return {'error': 'Session not found', 'key': key, 'nodes': [], 'edges': []}
    root = index[key].get('root_key') or key
    enrich = _load('enrich', {})
    assignments = _load('projects', {}).get('assignments', {})
    project_id = assignments.get(root)
    selected = {k: m for k, m in index.items() if (m.get('root_key') or k) == root}
    # Include ancestors when a partial transcript import lacks a root record.
    selected.setdefault(key, index[key])
    nodes, edges, missing = [], [], set()
    fields = ('id', 'source', 'title_seed', 'model_main', 'models', 'ts_first',
              'ts_last', 'status', 'node', 'execution_host', 'host_id', 'cwd', 'account_id', 'usage') + LINEAGE_FIELDS
    for node_key, meta in selected.items():
        node = {f: meta[f] for f in fields if f in meta}
        node.update(key=node_key, root_key=root, project_id=project_id,
                    title=(enrich.get(node_key) or {}).get('title') or meta.get('agent_label') or meta.get('title_seed') or node_key)
        node.update(resume_availability(meta))
        nodes.append(node)
        parent = meta.get('parent_key')
        if parent and parent != node_key:
            edges.append({'parent_key': parent, 'child_key': node_key})
            if parent not in selected:
                missing.add(parent)
    for parent in sorted(missing):
        nodes.append({'key': parent, 'root_key': root, 'missing': True,
                      'title': 'Transcript unavailable', 'project_id': project_id})
    nodes.sort(key=lambda n: (n.get('ts_first') or '', n['key']))
    return {'key': key, 'root_key': root, 'nodes': nodes, 'edges': edges,
            'missing_parent_keys': sorted(missing)}


ROUTES = {('GET', '/api/session_tree'): session_tree}
BACKGROUND = []
