import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { ModelOptionsResponse } from '../types';
import { CheckIcon, ChevronDownIcon, RefreshCwIcon, XIcon } from '../icons';

interface ModelPickerProps {
  currentModel: string | null;
  currentProvider: string | null;
  options: ModelOptionsResponse | null;
  loading: boolean;
  error: string | null;
  isSwitching: boolean;
  switchError: string | null;
  disabled?: boolean;
  onSelectModel: (provider: string, model: string) => Promise<boolean>;
  onReload: () => void;
}

export function ModelPicker({ currentModel, currentProvider, options, loading, error,
  isSwitching, switchError, disabled, onSelectModel, onReload }: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const providers = options?.providers.filter(p => p.configured) || [];
  const label = isSwitching ? '切换中…' : currentModel?.split('/').pop() || (loading ? '加载中…' : error ? '加载失败' : '选择模型');
  const close = () => { setOpen(false); button.current?.focus(); };

  useLayoutEffect(() => {
    if (!open || !button.current || !menu.current) return;
    const place = () => {
      const anchor = button.current!.getBoundingClientRect();
      const panel = menu.current!.getBoundingClientRect();
      setPosition({
        left: Math.max(8, Math.min(anchor.left, window.innerWidth - panel.width - 8)),
        top: Math.max(8, anchor.top >= panel.height + 16 ? anchor.top - panel.height - 8
          : Math.min(anchor.bottom + 8, window.innerHeight - panel.height - 8)),
      });
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => { window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
  }, [open, loading, error, switchError, options]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node) && !button.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    menu.current?.focus();
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);

  useEffect(() => { if (disabled && !isSwitching) setOpen(false); }, [disabled, isSwitching]);

  const navigate = (event: React.KeyboardEvent) => {
    const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('.model-item:not(:disabled)') || []);
    if (!items.length || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
      : event.key === 'ArrowUp' && index < 0 ? items.length - 1
      : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  };

  return (
    <div className="model-picker-wrapper">
      <button ref={button} type="button" className="model-picker-btn" disabled={disabled || isSwitching}
        title={currentModel ? `当前模型：${currentModel}` : '选择项目运行模型'}
        aria-label={`模型：${currentModel || label}`} aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen(!open)}
        onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true); } }}>
        <span className="model-picker-btn-name">模型 · {label}</span><ChevronDownIcon size={12} />
      </button>
      {open && createPortal(
        <div ref={menu} className="model-dropdown-menu" role="menu" aria-label="选择模型"
          tabIndex={-1} style={position} onKeyDown={navigate}>
          <div className="model-dropdown-header">
            <span>选择模型</span>
            <div className="model-dropdown-header-actions">
              <button type="button" aria-label="刷新模型列表" title="刷新模型列表"
                disabled={loading || isSwitching} onClick={onReload}><RefreshCwIcon size={14} /></button>
              <button type="button" aria-label="关闭模型列表" onClick={close}><XIcon size={14} /></button>
            </div>
          </div>
          {(error || switchError) && <div className="model-menu-error" role="alert">
            <span>{error || switchError}</span>
            {error && <button type="button" onClick={onReload} disabled={loading}>重试加载</button>}
          </div>}
          <div className="model-dropdown-body">
            {loading && <div className="model-menu-status" role="status">正在加载模型…</div>}
            {isSwitching && <div className="model-menu-status" role="status">正在切换模型…</div>}
            {providers.map(provider => <div className="model-provider-group" key={provider.slug}>
              <div className="model-provider-header">{provider.name}</div>
              {provider.models.map(model => {
                const selected = model === currentModel && provider.slug === currentProvider;
                return <button key={model} type="button" role="menuitemradio" aria-checked={selected}
                  className={`model-item ${selected ? 'active' : ''}`} data-is-current={String(selected)}
                  disabled={disabled || loading || isSwitching} onClick={async () => {
                    if (selected || await onSelectModel(provider.slug, model)) close();
                  }}><span>{model}</span>{selected && <CheckIcon size={14} />}</button>;
              })}
            </div>)}
            {!loading && !error && !providers.some(p => p.models.length) &&
              <div className="model-menu-status">暂无已配置的模型</div>}
          </div>
          <div className="model-menu-footer">
            <p>当前项目所有对话共用；重新连接后恢复默认。</p>
            <p>候选来自本机配置及内置目录。</p>
          </div>
        </div>, document.body)}
    </div>
  );
}
