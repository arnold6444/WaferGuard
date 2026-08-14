import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { hasHangul, translateLegacyStatic } from "./legacyTranslations";

const STORAGE_KEY = "waferguard.ui.preferences.v1";
const DEFAULTS = { language: "ko", theme: "light" };
const ORIGINAL_TEXT = new WeakMap();
const ORIGINAL_ATTRS = new WeakMap();
const TRANSLATABLE_ATTRS = ["placeholder", "title", "aria-label"];

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

function translateElementAttributes(element, language) {
  if (!(element instanceof Element) || element.closest("[data-no-auto-translate='true']")) return;
  const original = ORIGINAL_ATTRS.get(element) || {};
  if (language === "en") {
    for (const attribute of TRANSLATABLE_ATTRS) {
      const value = element.getAttribute(attribute);
      if (!value || !hasHangul(value)) continue;
      if (!(attribute in original)) original[attribute] = value;
      const translated = translateLegacyStatic(value);
      if (translated !== value) element.setAttribute(attribute, translated);
    }
    if (Object.keys(original).length) ORIGINAL_ATTRS.set(element, original);
  } else if (ORIGINAL_ATTRS.has(element)) {
    for (const [attribute, value] of Object.entries(original)) element.setAttribute(attribute, value);
    ORIGINAL_ATTRS.delete(element);
  }
}

function applyLegacyLanguage(root, language) {
  if (!root || typeof document === "undefined") return;
  const elementRoot = root.nodeType === Node.ELEMENT_NODE ? root : root.parentElement;
  if (elementRoot) {
    translateElementAttributes(elementRoot, language);
    elementRoot.querySelectorAll?.("[placeholder],[title],[aria-label]").forEach(element => translateElementAttributes(element, language));
  }

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const parent = node.parentElement;
    if (!parent || parent.closest("script,style,pre,code,[data-no-auto-translate='true']")) continue;
    if (language === "en") {
      const raw = node.nodeValue || "";
      if (!hasHangul(raw)) continue;
      ORIGINAL_TEXT.set(node, raw);
      const translated = translateLegacyStatic(raw);
      if (translated !== raw) node.nodeValue = translated;
    } else if (ORIGINAL_TEXT.has(node)) {
      const original = ORIGINAL_TEXT.get(node);
      if (node.nodeValue !== original) node.nodeValue = original;
      ORIGINAL_TEXT.delete(node);
    }
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

  useEffect(() => {
    const root = document.getElementById("root");
    if (!root) return undefined;
    applyLegacyLanguage(root, language);
    const observer = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === "characterData") {
          applyLegacyLanguage(record.target.parentElement, language);
          continue;
        }
        if (record.type === "attributes") {
          translateElementAttributes(record.target, language);
          continue;
        }
        for (const node of record.addedNodes) {
          if (node.nodeType === Node.TEXT_NODE) applyLegacyLanguage(node.parentElement, language);
          else if (node.nodeType === Node.ELEMENT_NODE) applyLegacyLanguage(node, language);
        }
      }
    });
    observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: TRANSLATABLE_ATTRS });
    return () => observer.disconnect();
  }, [language]);

  const setLanguage = useCallback((next) => setPreferences(prev => ({ ...prev, language: next === "en" ? "en" : "ko" })), []);
  const setTheme = useCallback((next) => setPreferences(prev => ({ ...prev, theme: next === "dark" ? "dark" : "light" })), []);
  const toggleTheme = useCallback(() => setPreferences(prev => ({ ...prev, theme: prev.theme === "dark" ? "light" : "dark" })), []);
  const text = useCallback((ko, en) => (language === "en" ? (en ?? ko) : ko), [language]);

  const value = useMemo(() => ({ language, theme, setLanguage, setTheme, toggleTheme, text }), [language, theme, setLanguage, setTheme, toggleTheme, text]);

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