/**
 * Operator-app strings in English, Hindi and Tamil (design.md "Copy voice": strings live in
 * web/src/i18n/*.json). en.json is the source of the keys; hi.json and ta.json are typed against it,
 * so a missing translation fails the build. The office app (/manager, /demo) stays in English.
 * Data from the backend (alert titles, handover summaries, module titles) is shown as sent.
 */
import { createElement, Fragment, useMemo, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { create } from 'zustand';
import en from './en.json';
import hiJson from './hi.json';
import taJson from './ta.json';

export type Key = keyof typeof en;
export type Lang = 'en' | 'hi' | 'ta';
export type Vars = Record<string, string | number>;

const hi: Record<Key, string> = hiJson;
const ta: Record<Key, string> = taJson;
const STRINGS: Record<Lang, Record<Key, string>> = { en, hi, ta };

export const LANGS: Lang[] = ['en', 'hi', 'ta'];
const KEY = 'cab-lang';

const isLang = (v: unknown): v is Lang => v === 'en' || v === 'hi' || v === 'ta';

function initial(): Lang {
  if (typeof window === 'undefined') return 'en';
  const q = new URLSearchParams(window.location.search).get('lang');
  if (isLang(q)) return q;
  try {
    const saved = window.localStorage.getItem(KEY);
    if (isLang(saved)) return saved;
  } catch {
    /* private mode */
  }
  return 'en';
}

const useLang = create<{ lang: Lang }>(() => ({ lang: initial() }));

/** Sets the cab language (profiles.preferred_language at sign-in, the login picker, the demo panel). */
export function setLanguage(lang: string | null | undefined): void {
  if (!isLang(lang)) return;
  try {
    window.localStorage.setItem(KEY, lang);
  } catch {
    /* private mode: this tab only */
  }
  useLang.setState({ lang });
}

export function getLanguage(): Lang {
  return useLang.getState().lang;
}

export function useLanguage(): Lang {
  return useLang((s) => s.lang);
}

function format(lang: Lang, key: Key, vars?: Vars): string {
  const s = STRINGS[lang][key] ?? en[key] ?? key;
  return vars ? s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)) : s;
}

export interface T {
  (key: Key, vars?: Vars): string;
  /** Same, with React nodes as values (numbers in `.reading` spans). */
  rich: (key: Key, vars: Record<string, ReactNode>) => ReactNode;
  /** A dynamic key such as `taskType.${type}`; falls back to `fallback` when it isn't a key. */
  dyn: (key: string, fallback: string) => string;
  lang: Lang;
}

function makeT(lang: Lang): T {
  const t = ((key: Key, vars?: Vars) => format(lang, key, vars)) as T;
  t.rich = (key, vars) => {
    const parts = format(lang, key).split(/\{(\w+)\}/);
    return createElement(
      Fragment,
      null,
      ...parts.map((p, i) => createElement(Fragment, { key: i }, i % 2 === 1 ? (vars[p] ?? `{${p}}`) : p)),
    );
  };
  t.dyn = (key, fallback) => (key in en ? format(lang, key as Key) : fallback);
  t.lang = lang;
  return t;
}

/** Outside React (alert steps, chat suggestions): the cab's current language. */
export function t(key: Key, vars?: Vars): string {
  return format(getLanguage(), key, vars);
}

/** Translator for this route: the cab language under /operator and on /login, English elsewhere. */
export function useT(): T {
  const { pathname } = useLocation();
  const lang = useLanguage();
  const l: Lang = pathname.startsWith('/operator') || pathname === '/login' ? lang : 'en';
  return useMemo(() => makeT(l), [l]);
}

if (typeof document !== 'undefined') {
  document.documentElement.lang = getLanguage();
  useLang.subscribe((s) => (document.documentElement.lang = s.lang));
}
