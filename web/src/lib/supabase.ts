import { createClient } from '@supabase/supabase-js';

// Anon key + RLS only; the service-role key never reaches the browser (CLAUDE.md rule 4).
// Switch to createClient<Database> once web/src/types/supabase.ts is generated.
const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

export const supabase = url && anonKey ? createClient(url, anonKey) : null;
