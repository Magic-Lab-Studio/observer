import { createContext, Fragment, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { detectLang, setLang as applyLang, type Lang } from './i18n';

const KEY = 'observer.lang';

interface Ctx {
  lang: Lang;
  setLang: (lang: Lang) => void;
}

const C = createContext<Ctx | null>(null);

export function useLocale(): Ctx {
  const v = useContext(C);
  if (!v) throw new Error('useLocale outside LocaleProvider');
  return v;
}

function readStored(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/**
 * Holds the chosen language. The tree is remounted when it changes: text is produced by the plain
 * `t()` function (also used by non-React helpers), so a remount is what makes every screen redraw.
 */
export function LocaleProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const initial = detectLang(readStored(), typeof navigator === 'undefined' ? '' : navigator.language);
    applyLang(initial);
    return initial;
  });

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    applyLang(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* storage may be blocked: the choice just does not persist */
    }
    setLangState(next);
  }, []);

  const value = useMemo(() => ({ lang, setLang }), [lang, setLang]);
  return (
    <C.Provider value={value}>
      <Fragment key={lang}>{children}</Fragment>
    </C.Provider>
  );
}
