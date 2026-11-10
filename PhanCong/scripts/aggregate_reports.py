#!/usr/bin/env python3
"""Aggregate each person's independent reports into two final docs.

Usage:
  python scripts/aggregate_reports.py
  python scripts/aggregate_reports.py --backend-repo /path/waimea-wavecast-backend \
      --frontend-repo /path/waimea-wavecast-frontend
  python scripts/aggregate_reports.py --output /path/to/deliverable/docs

The script only concatenates reports. It does NOT inspect code or run tests.
"""
from __future__ import annotations

import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
IDS = ('TL01', 'TL02', 'TL03', 'TL04', 'TL05')
REPORTS = {
    'STATUS': ('STATUS_REPORT.md', 'DANH SÁCH BÁO CÁO TÌNH TRẠNG'),
    'FUNCTIONS': ('COMPLETED_FUNCTIONS.md', 'DANH SÁCH FUNCTION / ENDPOINT / COMPONENT ĐÃ HOÀN THIỆN'),
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-repo', type=Path, help='Backend repo root with docs/contributions')
    parser.add_argument('--frontend-repo', type=Path, help='Frontend repo root with docs/contributions')
    parser.add_argument('--output', type=Path, default=HERE / 'docs', help='Final docs directory')
    return parser.parse_args()


def find_report(report_name: str, roots: list[Path]) -> Path | None:
    existing = [r / 'docs' / 'contributions' / report_name for r in roots]
    matches = [p for p in existing if p.is_file()]
    if len(matches) > 1:
        raise RuntimeError(f'Duplicated report {report_name}: {matches}. Remove stale copies before aggregation.')
    return matches[0] if matches else None


def main():
    args = parse_args()
    roots = [repo for repo in [args.backend_repo, args.frontend_repo] if repo is not None]
    if not roots:
        roots = [HERE]  # default: supplied blank templates inside this plan package
    args.output.mkdir(parents=True, exist_ok=True)
    for suffix, (filename, title) in REPORTS.items():
        sections = [f'# {title}\n',
                    '> Tổng hợp báo cáo cá nhân; **không** tự xác nhận code/test đã đạt. Reviewer cần đối chiếu Pull Request và log kiểm thử.\n']
        for ident in IDS:
            path = find_report(f'{ident}_{suffix}.md', roots)
            sections.extend(['\n---\n', f'## {ident}\n'])
            if path:
                sections.append(path.read_text(encoding='utf-8'))
            else:
                sections.append('**CHƯA NHẬN BÁO CÁO.**\n')
        out = args.output / filename
        out.write_text('\n'.join(sections), encoding='utf-8')
        print(f'Wrote {out}')


if __name__ == '__main__':
    main()
