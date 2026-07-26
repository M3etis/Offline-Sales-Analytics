/**
 * Shared domain types used across multiple feature modules.
 * Feature-specific types should live in their own features/<name>/types/.
 */

export interface Relationship {
  from_table: string;
  from_col: string;
  to_table: string;
  to_col: string;
}

export interface Dataset {
  id: string;
  name: string;
  rows: number;
  uploaded_at: string;
  source_group?: string | null;
  source_file?: string | null;
  relationships?: Relationship[] | null;
  manifest_status?: 'ready' | 'stale' | 'not_found';
}

export interface DatasetGroup {
  group_id: string;
  source_file: string;
  tables: Dataset[];
  relationships: Relationship[];
}

export interface TableRow {
  [key: string]: string | number;
}

export interface TableResponse {
  data: TableRow[];
  columns?: string[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface AskResponse {
  answer: string;
  sql: string | null;
  intent: string | null;
  data: Record<string, unknown>[] | null;
  chart_type: string | null;
  error: string | null;
  processing_time: number | null;
  is_cached?: boolean;
  cached_at?: string;
}

export interface ChatSession {
  id: string;
  title: string;
  dataset_id?: string;
  created_at: string;
  updated_at: string;
  status?: 'ok' | 'no_response' | 'error';
}

export interface ChatMessageItem {
  id: string;
  role: string;
  content: string;
  data_summary: string | null;
  feedback: string | null;
  created_at: string;
}

export interface SessionDetails {
  session: ChatSession;
  messages: ChatMessageItem[];
}

export interface PaginatedSessions {
  sessions: ChatSession[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface UserOut {
  id: string;
  username: string;
  full_name: string;
  role: string;
  permissions: string[];
  created_at: string;
}

export interface UserCreate {
  username: string;
  password: string;
  full_name: string;
  role: string;
  permissions: string[];
}

export interface UserUpdate {
  full_name?: string;
  role?: string;
  permissions?: string[];
}
