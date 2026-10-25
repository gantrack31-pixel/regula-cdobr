export type Role = 'employee' | 'curator' | 'admin';
export interface User { id: string; username: string; name: string; role: Role; demo_mode: boolean }
export interface Health { status: string; model: string; model_available: boolean; embedding_model: string; embedding_available: boolean; mode: string; source_count: number; chunk_count: number; demo_mode: boolean }
export interface Citation { id: string; document_id: string; version_id: string; title: string; section: string; page: number | null; quote: string; score: number; source_url: string; context?: string }
export interface RouteStep { order: number; name: string; reason: string; source: unknown }
export interface RouteResult { title: string; steps: RouteStep[]; departments: { name: string; reason: string; source: unknown; conditional?: boolean }[]; notes: string[]; missing_questions: string[]; full_approval: boolean }
export interface ChatResponse { answer_kind?: string; answer_label?: string; conversation_id: string; answer: string; status: string; mode: string; citations: Citation[]; suggested_questions: string[]; template_suggestions: string[]; route?: RouteResult; elapsed_ms: number; trace: Record<string, unknown> }
export interface Message { id: string; role: string; content: string; response?: ChatResponse; created_at?: string }
export interface Conversation { id: string; title: string; updated_at: string }
export interface Field { key: string; label: string; type: 'text' | 'textarea' | 'date' | 'boolean' | 'people'; required: boolean; placeholder?: string; help?: string; default?: unknown }
export interface Template { id: string; title: string; description: string; document_type: string; version: string; source_document_id?: string; fields: Field[]; hints: string[]; tags: string[] }
export interface Validation { field: string; severity: string; message: string; rule?: string }
export interface Draft { revision: number; confirmed_fields: string[]; unconfirmed_fields: string[]; field_evidence: Record<string, string>; id: string; template_id: string; title: string; values: Record<string, unknown>; missing_fields: string[]; validation: Validation[]; preview: string; readiness: number; updated_at: string; route?: RouteResult }
export interface Version { id: string; version: number | string; status: string; created_at: string; effective_from: string; filename: string; chunk_count: number }
export interface Source { id: string; title: string; kind: string; access: string; active_version_id: string; version: string | number; chunk_count: number; updated_at: string; synthetic: boolean; versions: Version[]; chunks?: { id: string; section: string; page: number | null; text: string; version_id: string }[] }
export interface ReviewResult { id: string; filename: string; document_type: string; summary: string; score: number; findings: { id: string; severity: string; title: string; detail: string; source: unknown; location?: string; suggestion?: string }[]; route: RouteResult; extracted_text: string; checks: { name: string; status: string }[]; limitations: string[] }
export type Page = 'assistant' | 'documents' | 'review' | 'route' | 'sources' | 'audit' | 'demo';

export interface Proposal { values: Record<string, unknown>; evidence: Record<string, string>; conflicts: string[]; base_revision: number; mode: string }
