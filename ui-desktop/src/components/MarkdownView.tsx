import React, { useState } from 'react';
import { CheckIcon, CopyIcon } from '../icons';
import { parseTable } from '../utils/markdownTable';
import './MarkdownView.css';

interface MarkdownViewProps {
  content: string;
}

interface CodeBlockProps {
  code: string;
  language?: string;
}

function CodeBlock({ code, language }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };

  return (
    <div className="code-block-container">
      <div className="code-block-header">
        <span>{language || 'code'}</span>
        <button
          type="button"
          onClick={handleCopy}
          className="code-block-copy-btn"
          title="复制内容"
        >
          {copied ? (
            <>
              <CheckIcon size={12} />
              <span>已复制</span>
            </>
          ) : (
            <>
              <CopyIcon size={12} />
              <span>复制</span>
            </>
          )}
        </button>
      </div>
      <pre className="code-block-content">
        <code>{code}</code>
      </pre>
    </div>
  );
}

function renderInline(text: string): React.ReactNode[] {
  // Keep inline formatting shared between paragraphs and table cells.
  const parts: React.ReactNode[] = [];
  const regex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]\n]+\]\((?:[^\s()]+|\([^\s()]*\))+\))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.substring(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(
        <code key={match.index} className="inline-code">
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(
        <strong key={match.index}>
          {token.slice(2, -2)}
        </strong>
      );
    } else if (token.startsWith('*') && token.endsWith('*')) {
      parts.push(
        <em key={match.index}>
          {token.slice(1, -1)}
        </em>
      );
    } else if (token.startsWith('[')) {
      const endLabel = token.indexOf('](');
      const label = token.slice(1, endLabel);
      const href = token.slice(endLabel + 2, -1);
      let safe = false;
      try {
        safe = ['http:', 'https:', 'mailto:'].includes(new URL(href, 'https://rincode.invalid').protocol);
      } catch { /* Invalid destinations remain readable text. */ }
      parts.push(safe ? (
        <a key={match.index} href={href} target="_blank" rel="noreferrer noopener">{renderInline(label)}</a>
      ) : label);
    } else {
      parts.push(token);
    }
    lastIndex = regex.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(text.substring(lastIndex));
  }

  return parts;
}

export function MarkdownView({ content }: MarkdownViewProps) {
  if (!content) return null;

  const lines = content.split('\n');
  const elements: React.ReactNode[] = [];
  let inCodeBlock = false;
  let codeFence = '';
  let codeLanguage = '';
  let codeBuffer: string[] = [];
  let currentList: { type: 'ul' | 'ol'; items: string[]; start?: number } | null = null;

  const flushList = () => {
    if (!currentList) return;
    if (currentList.type === 'ul') {
      elements.push(
        <ul key={`list-${elements.length}`}>
          {currentList.items.map((item, idx) => (
            <li key={idx}>{renderInline(item)}</li>
          ))}
        </ul>
      );
    } else {
      elements.push(
        <ol key={`list-${elements.length}`} start={currentList.start}>
          {currentList.items.map((item, idx) => (
            <li key={idx}>{renderInline(item)}</li>
          ))}
        </ol>
      );
    }
    currentList = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    const fence = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (inCodeBlock) {
      if (fence && fence[1][0] === codeFence[0] && fence[1].length >= codeFence.length && !fence[2].trim()) {
        inCodeBlock = false;
        elements.push(
          <CodeBlock
            key={`code-${elements.length}`}
            code={codeBuffer.join('\n')}
            language={codeLanguage}
          />
        );
        codeBuffer = [];
        codeLanguage = '';
      } else {
        codeBuffer.push(line);
      }
      continue;
    }

    if (fence) {
      flushList();
      inCodeBlock = true;
      codeFence = fence[1];
      codeLanguage = fence[2].trim();
      codeBuffer = [];
      continue;
    }

    const table = parseTable(lines, i);
    if (table) {
      flushList();
      elements.push(
        <div key={`table-${elements.length}`} className="markdown-table-wrapper">
          <table className="markdown-table">
            <thead><tr>{table.headers.map((cell, column) => (
              <th key={column} scope="col" style={{ textAlign: table.alignments[column] }}>{renderInline(cell)}</th>
            ))}</tr></thead>
            {table.rows.length > 0 && <tbody>{table.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>{row.map((cell, column) => (
                <td key={column} style={{ textAlign: table.alignments[column] }}>{renderInline(cell)}</td>
              ))}</tr>
            ))}</tbody>}
          </table>
        </div>
      );
      i = table.nextLine - 1;
      continue;
    }

    // Unordered list
    const ulMatch = line.match(/^(\s*)[-*+]\s+(.*)$/);
    if (ulMatch) {
      if (!currentList || currentList.type !== 'ul') {
        flushList();
        currentList = { type: 'ul', items: [] };
      }
      currentList.items.push(ulMatch[2]);
      continue;
    }

    // Ordered list
    const olMatch = line.match(/^(\s*)(\d+)\.\s+(.*)$/);
    if (olMatch) {
      if (!currentList || currentList.type !== 'ol') {
        flushList();
        currentList = { type: 'ol', items: [], start: Number(olMatch[2]) };
      }
      currentList.items.push(olMatch[3]);
      continue;
    }

    flushList();

    // Headings
    if (line.startsWith('### ')) {
      elements.push(
        <h3 key={`h3-${elements.length}`}>{renderInline(line.slice(4))}</h3>
      );
      continue;
    }
    if (line.startsWith('## ')) {
      elements.push(
        <h2 key={`h2-${elements.length}`}>{renderInline(line.slice(3))}</h2>
      );
      continue;
    }
    if (line.startsWith('# ')) {
      elements.push(
        <h1 key={`h1-${elements.length}`}>{renderInline(line.slice(2))}</h1>
      );
      continue;
    }

    // Blockquote
    if (line.startsWith('> ')) {
      elements.push(
        <blockquote key={`quote-${elements.length}`}>
          {renderInline(line.slice(2))}
        </blockquote>
      );
      continue;
    }

    // Empty line
    if (!line.trim()) {
      continue;
    }

    // Paragraph
    elements.push(
      <p key={`p-${elements.length}`}>{renderInline(line)}</p>
    );
  }

  if (inCodeBlock) {
    elements.push(
      <CodeBlock
        key={`code-${elements.length}`}
        code={codeBuffer.join('\n')}
        language={codeLanguage}
      />
    );
  }

  flushList();

  return <div className="markdown-view">{elements}</div>;
}
