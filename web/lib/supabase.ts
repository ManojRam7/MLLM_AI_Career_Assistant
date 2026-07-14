import { createClient, SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";

export const supabaseConfigured = Boolean(url && key);

// Only construct the client when configured — createClient throws on an empty URL,
// which would crash the "Connect Supabase" setup screen. Callers guard on supabaseConfigured.
export const supabase: SupabaseClient = supabaseConfigured
  ? createClient(url, key, { auth: { persistSession: false } })
  : (null as unknown as SupabaseClient);
