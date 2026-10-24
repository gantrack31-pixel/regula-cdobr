import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { AlertCircle, RotateCcw } from 'lucide-react';

export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('Regula interface error', error, info.componentStack); }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div className="recovery-screen" role="alert"><AlertCircle size={36}/><h1>Не удалось показать страницу</h1><p>Сохранённые проекты и диалоги остаются в рабочем пространстве. Обновите страницу, чтобы продолжить.</p><button className="button primary" onClick={() => window.location.reload()}><RotateCcw size={17}/>Обновить страницу</button></div>;
  }
}
