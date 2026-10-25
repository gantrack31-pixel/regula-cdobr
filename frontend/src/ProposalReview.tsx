import { Check, Quote, Undo2 } from 'lucide-react';
import type { Proposal, Template } from './types';

export function displayValue(value: unknown): string {
  if (value === true) return 'Да';
  if (value === false) return 'Нет';
  if (Array.isArray(value)) return value.map(person =>
    [person.role, person.name, person.position, person.department].filter(Boolean).join(' · ')).join('\n');
  return String(value ?? '') || 'Не заполнено';
}

export default function ProposalReview({ proposal, template, values, chosen, onToggle, onApply, onCancel, busy }: {
  proposal: Proposal; template: Template; values: Record<string, unknown>; chosen: string[];
  onToggle: (key: string) => void; onApply: () => void; onCancel: () => void; busy: boolean;
}) {
  const label = (key: string) => template.fields.find(field => field.key === key)?.label || key;
  return <section className="proposal-review" aria-label="Предложенные изменения">
    <div className="section-heading"><h2>Сначала проверьте сведения</h2><span className="proposal-count">{chosen.length} выбрано</span></div>
    <p>Сопоставьте каждое значение с вашей просьбой. Снимите выбор с того, что нужно уточнить.</p>
    {proposal.conflicts.length > 0 && <div className="notice warning">Противоречивые сведения: {proposal.conflicts.map(label).join(', ')}. Укажите одно значение для каждого поля.</div>}
    {!Object.keys(proposal.values).length && <div className="notice">Новых однозначных значений нет. Укажите сведения в форме или напишите «название поля: значение».</div>}
    <div className="proposal-list">{Object.entries(proposal.values).map(([key, value]) => <article className={`proposal-card ${chosen.includes(key) ? 'selected' : ''}`} key={key}>
      <label className="proposal-choice"><input type="checkbox" disabled={busy} checked={chosen.includes(key)} onChange={() => onToggle(key)}/><strong>{label(key)}</strong></label>
      <div className="proposal-comparison"><div><small>Сейчас</small><p>{displayValue(values[key])}</p></div><div><small>Предложено</small><p>{displayValue(value)}</p></div></div>
      <blockquote><Quote size={14}/><span>{proposal.evidence[key] || 'Уточните это значение вручную.'}</span></blockquote>
    </article>)}</div>
    <div className="button-group"><button className="button primary" disabled={busy || !chosen.length} onClick={onApply}><Check size={17}/>Применить выбранное</button><button className="button subtle" disabled={busy} onClick={onCancel}><Undo2 size={16}/>Отменить предложения</button></div>
  </section>;
}
