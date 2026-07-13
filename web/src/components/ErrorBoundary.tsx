import { Component, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface Props {
  children: ReactNode;
  label: string;
}

interface State {
  failed: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(): void {
    // Deliberately do not serialize the error or component stack into the UI.
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <section className="render-failure panel" role="alert">
        <AlertTriangle size={22} />
        <div>
          <strong>Не удалось безопасно отобразить раздел «{this.props.label}».</strong>
          <p>Локальные данные не изменены. Перезагрузите проверенный срез, чтобы продолжить.</p>
        </div>
        <button className="secondary-button" type="button" onClick={() => window.location.reload()}>
          <RefreshCw size={15} /> Перезагрузить
        </button>
      </section>
    );
  }
}
