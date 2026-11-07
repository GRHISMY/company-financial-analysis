#!/usr/bin/env python3
"""Render labelled, reproducible financial small multiples as standalone SVG."""
import argparse
import html
import json
import math
from pathlib import Path

COLORS = ['#176b87', '#c97637', '#479071', '#805ba5', '#b54652']


def esc(value):
    return html.escape(str(value), quote=True)


def render(data):
    panels = data['panels']
    if not panels:
        raise ValueError('At least one panel required')
    width, panel_height = 1240, 310
    height = 100 + math.ceil(len(panels) / 2) * panel_height + 70
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#f7f8fa"/>',
             '<style>text{font-family:"PingFang SC","Microsoft YaHei",sans-serif;fill:#223345}.muted{fill:#627587}</style>',
             f'<text x="42" y="43" font-size="25" font-weight="600">{esc(data["title"])}</text>',
             f'<text x="42" y="72" font-size="13" class="muted">{esc(data.get("subtitle", ""))}</text>']
    for index, panel in enumerate(panels):
        labels, series = panel['labels'], panel['series']
        if not labels or not series:
            raise ValueError('Each panel requires labels and series')
        values = []
        for item in series:
            if len(item['values']) != len(labels):
                raise ValueError('Series length differs from labels')
            for value in item['values']:
                if value is not None:
                    value = float(value)
                    if not math.isfinite(value):
                        raise ValueError('Non-finite chart value')
                    values.append(value)
        if not values:
            raise ValueError('Panel contains no disclosed values')
        ox, oy = 28 + index % 2 * 608, 96 + index // 2 * panel_height
        parts.extend([f'<rect x="{ox}" y="{oy}" width="592" height="294" rx="10" fill="white"/>',
                      f'<text x="{ox+20}" y="{oy+29}" font-size="17" font-weight="600">{esc(panel["title"])}</text>',
                      f'<text x="{ox+20}" y="{oy+50}" font-size="12" class="muted">{esc(panel.get("unit", ""))}</text>'])
        left, top, w, h = ox + 66, oy + 93, 496, 154
        lower, upper = min(0, min(values)), max(0, max(values))
        span = upper - lower or 1
        upper += span * .12
        if lower < 0:
            lower -= span * .08
        span = upper - lower
        def yy(v):
            return top + h - (float(v) - lower) / span * h
        def xx(i):
            return left + i * w / max(1, len(labels)-1)
        for tick in range(5):
            value = lower + span * tick / 4
            y = yy(value)
            parts.extend([f'<line x1="{left}" y1="{y:.2f}" x2="{left+w}" y2="{y:.2f}" stroke="#e6ebef"/>',
                          f'<text x="{left-9}" y="{y+4:.2f}" text-anchor="end" font-size="11" class="muted">{value:,.0f}</text>'])
        for i, label in enumerate(labels):
            parts.append(f'<text x="{xx(i):.2f}" y="{top+h+20}" text-anchor="middle" font-size="11" class="muted">{esc(label)}</text>')
        for si, item in enumerate(series):
            color = COLORS[si % len(COLORS)]
            legend_x = ox + 20 + si * 180
            parts.extend([f'<line x1="{legend_x}" y1="{oy+70}" x2="{legend_x+21}" y2="{oy+70}" stroke="{color}" stroke-width="3"/>',
                          f'<text x="{legend_x+28}" y="{oy+74}" font-size="12">{esc(item["name"])}</text>'])
            segment = []
            for i, value in enumerate(item['values'] + [None]):
                if value is None:
                    if len(segment) > 1:
                        parts.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="{color}" stroke-width="2.5"/>')
                    segment = []
                    continue
                x, y = xx(i), yy(value)
                segment.append(f'{x:.2f},{y:.2f}')
                parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" fill="{color}"><title>{esc(item["name"])} {esc(labels[i])}: {esc(value)}</title></circle>')
    parts.append(f'<text x="42" y="{height-29}" font-size="12" class="muted">{esc(data.get("source", ""))}</text>')
    parts.append('</svg>')
    return '\n'.join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='JSON with title, panels[{title,unit,labels,series[{name,values}]}], source')
    parser.add_argument('--output', required=True, help='Standalone .svg output; null values remain gaps')
    args = parser.parse_args()
    output = Path(args.output)
    if output.suffix.lower() != '.svg':
        parser.error('Output must use .svg extension')
    result = render(json.loads(Path(args.input).read_text(encoding='utf-8')))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result, encoding='utf-8')
    print(json.dumps({'output':str(output), 'bytes':output.stat().st_size}, ensure_ascii=False))


if __name__ == '__main__':
    main()
