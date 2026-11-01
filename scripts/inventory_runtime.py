"""Generate a static, reviewable inventory of runtime dependency call sites.

AST inventory is deliberately conservative: dynamic imports, aliases and
indirect factories still require manual review. No project data are read.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = {
    'get_default_memory_manager': 'memory', 'get_default_conversation_context': 'conversation',
    'get_default_task_manager': 'tasks', 'get_default_scheduler': 'scheduler',
    'get_default_todo_manager': 'todo', 'get_default_routine_manager': 'routines',
    'get_default_mode_manager': 'modes', 'get_default_music_manager': 'music',
    'get_default_activity_log': 'activity', 'configure_default_task_manager': 'tasks',
    'start_default_scheduler': 'scheduler', 'set_default_memory_manager': 'memory',
    'set_default_conversation_context': 'conversation', 'set_default_scheduler': 'scheduler',
    'set_default_task_manager': 'tasks', 'set_default_todo_manager': 'todo',
    'set_default_routine_manager': 'routines', 'set_default_mode_manager': 'modes',
    'set_default_music_manager': 'music', 'set_default_activity_log': 'activity',
    'CoreRuntime': 'runtime', 'AudioIO': 'audio', 'GeminiLive': 'provider',
    'MemoryManager': 'memory', 'ConversationContext': 'conversation', 'TaskManager': 'tasks',
    'Scheduler': 'scheduler', 'TodoManager': 'todo', 'RoutineManager': 'routines',
    'JarvisModeManager': 'modes', 'MusicManager': 'music', 'ActivityLog': 'activity',
}
GLOBALS = {'_DEFAULT', '_DEFAULT_MANAGER', '_DEFAULT_SCHEDULER', '_DEFAULT_LOG', 'RUNTIME',
           '_DIALOG', 'LIVE', 'DASHBOARD', 'INTERFACE_MODE', 'DESKTOP_EVENTS', 'VISIBILITY',
           '_HOOKS', '_WINDOWS_QUEUE', '_WINDOWS_WORKER', '_ACTIVE_RUN', '_LISTENERS',
           '_TIMERS', '_HISTORY', '_TOOL_FUNCTIONS', '_TOOL_DECLARATIONS', '_writer_override'}


class Inventory(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.scope = []
        self.rows = []

    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Call(self, node):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ''
        service = SERVICES.get(name)
        if name == '_resolve' and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            self.rows.append((self.path, node.lineno, '.'.join(self.scope) or '<module>',
                              node.args[1].value, name, 'registre injecté ou fallback autonome',
                              'injection explicite si présente ; getter historique sinon'))
        if service:
            if name.startswith('get_default_'):
                origin, risk = 'getter global historique', 'instance partagée ou création paresseuse ; setter peut diverger'
            elif name.startswith(('configure_default_', 'start_default_', 'set_default_')):
                origin, risk = 'configuration globale historique', 'cycle de vie et remplacement à coordonner'
            else:
                origin, risk = 'construction directe', 'nouvelle instance ; vérifier propriétaire et appelants'
            self.rows.append((self.path, node.lineno, '.'.join(self.scope) or '<module>', service, name, origin, risk))
        # Registry use is useful alongside legacy accesses in the same report.
        if isinstance(func, ast.Attribute) and func.attr in ('require', 'register', 'register_lazy'):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                obj = ast.unparse(func.value)
                if 'service' in obj or obj == 'registry':
                    self.rows.append((self.path, node.lineno, '.'.join(self.scope) or '<module>',
                                      node.args[0].value, func.attr, 'registre injecté',
                                      'dépendance indisponible après fermeture ou avant initialisation'))
        self.generic_visit(node)


def main():
    rows, globals_found = [], []
    for directory in ('src', 'UI'):
        for path in sorted((ROOT / directory).rglob('*.py')):
            rel = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=rel)
            visitor = Inventory(rel)
            visitor.visit(tree)
            rows.extend(visitor.rows)
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id in GLOBALS:
                            globals_found.append((rel, node.lineno, target.id))
    print('# Inventaire statique des dépendances et états de processus\n')
    print('Généré par `python scripts/inventory_runtime.py`. Les fonctions/méthodes indiquent le consommateur.')
    print('La recherche AST couvre ces noms et constructions explicites, pas les appels dynamiques, alias ou callbacks indirects ; '
          'voir les tableaux de responsabilités et exceptions dans `validation-finale-lot-01-5.md`.')
    print('Un getter historique reste une compatibilité à migrer, pas une preuve que le registre possède le service.\n')
    print('| Fichier:ligne | Consommateur | Service | Appel | Origine | Risque | Migration / raison de conservation |')
    print('|---|---|---|---|---|---|---|')
    for path, line, scope, service, name, origin, risk in rows:
        if origin == 'registre injecté':
            disposition = 'migré ; disponibilité et arrêt à gérer'
        elif origin == 'registre injecté ou fallback autonome':
            disposition = 'injecté pour les appels Core ; compatibilité hors Core'
        elif path == 'src/core/runtime.py':
            disposition = 'compatibilité des factories ; supprimer après migration des propriétaires'
        elif path == 'src/tools.py':
            disposition = 'injecter au dispatcher ; préserver API routines/outils historique'
        elif path.startswith('UI/'):
            disposition = 'injecter au point d’entrée ; fallback autonome conservé si présent'
        elif name.startswith('get_default_'):
            disposition = 'injectable ; conservé pour les appelants historiques'
        else:
            disposition = 'examiner propriétaire avant de remplacer ou retirer'
        print(f'| `{path}:{line}` | `{scope}` | {service} | `{name}` | {origin} | {risk} | {disposition} |')
    print('\n## États globaux recensés\n')
    print('| Fichier:ligne | État |')
    print('|---|---|')
    for path, line, name in globals_found:
        print(f'| `{path}:{line}` | `{name}` |')


if __name__ == '__main__':
    main()
