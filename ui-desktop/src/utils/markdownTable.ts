export type TableAlignment = 'left' | 'center' | 'right' | undefined;

const startsBlock = (line: string) =>
  /^ {0,3}(?:#{1,6}\s|>|[-+*]\s|\d+[.)]\s|`{3,}|~{3,})/.test(line) ||
  /^ {0,3}(?:(?:-\s*){3,}|(?:_\s*){3,}|(?:\*\s*){3,})$/.test(line);

function splitRow(line: string): { cells: string[]; hasPipe: boolean } {
  const source = line.trim();
  const cells = [''];
  let hasPipe = false;
  let trailingPipe = false;
  for (let i = 0; i < source.length; i++) {
    const char = source[i];
    if (char === '\\' && (source[i + 1] === '|' || source[i + 1] === '\\')) {
      cells[cells.length - 1] += source[i + 1] === '|' ? '|' : '\\\\';
      i++;
    } else if (char === '|') {
      hasPipe = true;
      trailingPipe = i === source.length - 1;
      cells.push('');
    } else {
      cells[cells.length - 1] += char;
    }
  }
  if (source.startsWith('|')) cells.shift();
  if (trailingPipe) cells.pop();
  return { cells: cells.map((cell) => cell.trim()), hasPipe };
}

// Only look ahead after MarkdownView has ruled out fenced code blocks.
export function parseTable(lines: string[], start: number) {
  if (start + 1 >= lines.length || startsBlock(lines[start]) ||
    /^ {4}|^\t/.test(lines[start]) || /^ {4}|^\t/.test(lines[start + 1])) return null;
  const header = splitRow(lines[start]);
  const delimiter = splitRow(lines[start + 1]);
  if (!(header.hasPipe || delimiter.hasPipe) || !header.cells.length || header.cells.length !== delimiter.cells.length ||
    !delimiter.cells.every((cell) => /^:?-+:?$/.test(cell))) return null;

  const alignments: TableAlignment[] = delimiter.cells.map((cell) =>
    cell.startsWith(':') ? (cell.endsWith(':') ? 'center' : 'left') : cell.endsWith(':') ? 'right' : undefined);
  const rows: string[][] = [];
  let nextLine = start + 2;
  while (nextLine < lines.length) {
    const line = lines[nextLine];
    if (!line.trim() || startsBlock(line)) break;
    const cells = splitRow(line).cells;
    rows.push(header.cells.map((_, column) => cells[column] || ''));
    nextLine++;
  }
  return { headers: header.cells, alignments, rows, nextLine };
}
