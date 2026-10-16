"""Guard local and CI commits: read staged diff → detect credentials → report status."""

import ast
import re
import subprocess


def main() -> int:
    """Reject credential shapes and added literal secret assignments.

    Args:
        None.
    Returns:
        Zero for a clean staged diff, one if a credential is detected.
    """
    diff = subprocess.check_output(['git', 'diff', '--cached', '--no-ext-diff'], text=True)
    if re.search(r'AIza[0-9A-Za-z_-]{35}|sk-[A-Za-z0-9]{20,}', diff):
        print('Credential-shaped value detected in staged diff; commit aborted.')
        return 1
    for line in diff.splitlines():
        if not line.startswith('+') or line.startswith('+++'):
            continue
        # Parse assignments instead of flagging environment variable names.
        try:
            tree = ast.parse(line[1:].strip())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and re.search(r'_(KEY|PASSWORD|SECRET)$', target.id):
                    if isinstance(node.value, ast.Constant) and node.value.value not in ('', '********', None):
                        print(f'Literal credential assignment to {target.id}; commit aborted.')
                        return 1
    print('Staged credential scan passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
