import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const STORAGE_KEY = "waferguard.ui.preferences.v1";
const DEFAULTS = { language: "ko", theme: "light" };

function loadPreferences() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    return {
      language: parsed.language === "en" ? "en" : "ko",
      theme: parsed.theme === "dark" ? "dark" : "light",
    };
  } catch {
    return DEFAULTS;
  }
}

const UiContext = createContext(null);

export function UiProvider({ children }) {
  const [preferences, setPreferences] = useState(loadPreferences);
  const { language, theme } = preferences;

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.setAttribute("lang", language === "ko" ? "ko" : "en");
    document.documentElement.style.colorScheme = theme;
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences)); } catch { /* ignore */ }
  }, [language, theme, preferences]);

  const setLanguage = useCallback((next) => {
    setPreferences(prev => ({ ...prev, language: next === "en" ? "en" : "ko" }));
  }, []);

  const setTheme = useCallback((next) => {
    setPreferences(prev => ({ ...prev, theme: next === "dark" ? "dark" : "light" }));
  }, []);

  const toggleTheme = useCallback(() => {
    setPreferences(prev => ({ ...prev, theme: prev.theme === "dark" ? "light" : "dark" }));
  }, []);

  const text = useCallback((ko, en) => (language === "en" ? (en ?? ko) : ko), [language]);

  const value = useMemo(() => ({
    language,
    theme,
    setLanguage,
    setTheme,
    toggleTheme,
    text,
  }), [language, theme, setLanguage, setTheme, toggleTheme, text]);

  return <UiContext.Provider value={value}>{children}</UiContext.Provider>;
}

export function useUi() {
  const value = useContext(UiContext);
  if (!value) throw new Error("useUi must be used within UiProvider");
  return value;
}

export function Localized({ ko, en, as: Component = React.Fragment, ...props }) {
  const { text } = useUi();
  if (Component === React.Fragment) return <>{text(ko, en)}</>;
  return <Component {...props}>{text(ko, en)}</Component>;
}
