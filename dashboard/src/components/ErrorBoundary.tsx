import React from 'react';
import { t } from '../i18n';

interface Props {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback || (
          <div className="p-8 text-center">
            <h2 className="text-xl font-semibold text-bad mb-2">
              
              {t("Algo salió mal")}
            </h2>
            <p className="text-dim text-sm">
              {this.state.error?.message || t('Ocurrió un error inesperado')}
            </p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="mt-4 px-4 py-2 bg-raised hover:bg-line-strong rounded text-sm"
            >
              
              {t("Reintentar")}
            </button>
          </div>
        )
      );
    }

    return this.props.children;
  }
}
