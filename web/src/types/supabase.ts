// Generated from the live database (public schema) in the `supabase gen types typescript` shape.
// Regenerate: supabase gen types typescript --linked > web/src/types/supabase.ts  (CLAUDE.md rule 3)
export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "12.2.3"
  }
  public: {
    Tables: {
      alerts: {
        Row: {
          acknowledged_at: string | null
          acknowledged_by: string | null
          alert_code: string
          anomaly_score: number | null
          category: Database["public"]["Enums"]["alert_category"]
          evidence: Json | null
          id: number
          machine_id: string | null
          message: string
          operator_id: string | null
          recommended_action: string | null
          resolved_at: string | null
          severity: Database["public"]["Enums"]["severity_level"]
          site_id: string
          source: Database["public"]["Enums"]["alert_source"]
          stage: Database["public"]["Enums"]["alert_stage"]
          title: string
          ts: string
        }
        Insert: {
          acknowledged_at?: string | null
          acknowledged_by?: string | null
          alert_code: string
          anomaly_score?: number | null
          category: Database["public"]["Enums"]["alert_category"]
          evidence?: Json | null
          id?: never
          machine_id?: string | null
          message: string
          operator_id?: string | null
          recommended_action?: string | null
          resolved_at?: string | null
          severity: Database["public"]["Enums"]["severity_level"]
          site_id: string
          source: Database["public"]["Enums"]["alert_source"]
          stage?: Database["public"]["Enums"]["alert_stage"]
          title: string
          ts?: string
        }
        Update: {
          acknowledged_at?: string | null
          acknowledged_by?: string | null
          alert_code?: string
          anomaly_score?: number | null
          category?: Database["public"]["Enums"]["alert_category"]
          evidence?: Json | null
          id?: never
          machine_id?: string | null
          message?: string
          operator_id?: string | null
          recommended_action?: string | null
          resolved_at?: string | null
          severity?: Database["public"]["Enums"]["severity_level"]
          site_id?: string
          source?: Database["public"]["Enums"]["alert_source"]
          stage?: Database["public"]["Enums"]["alert_stage"]
          title?: string
          ts?: string
        }
        Relationships: [
          {
            foreignKeyName: "alerts_acknowledged_by_fkey"
            columns: ["acknowledged_by"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "alerts_machine_id_fkey"
            columns: ["machine_id"]
            isOneToOne: false
            referencedRelation: "machines"
            referencedColumns: ["machine_id"]
          },
          {
            foreignKeyName: "alerts_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
          {
            foreignKeyName: "alerts_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
      chat_messages: {
        Row: {
          content: string
          created_at: string
          id: number
          operator_id: string | null
          role: string
          session_id: string
          sources: Json | null
        }
        Insert: {
          content: string
          created_at?: string
          id?: never
          operator_id?: string | null
          role: string
          session_id: string
          sources?: Json | null
        }
        Update: {
          content?: string
          created_at?: string
          id?: never
          operator_id?: string | null
          role?: string
          session_id?: string
          sources?: Json | null
        }
        Relationships: [
          {
            foreignKeyName: "chat_messages_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
        ]
      }
      document_chunks: {
        Row: {
          chunk_index: number
          content: string
          document_id: number
          embedding: string
          id: number
          metadata: Json | null
        }
        Insert: {
          chunk_index: number
          content: string
          document_id: number
          embedding: string
          id?: never
          metadata?: Json | null
        }
        Update: {
          chunk_index?: number
          content?: string
          document_id?: number
          embedding?: string
          id?: never
          metadata?: Json | null
        }
        Relationships: [
          {
            foreignKeyName: "document_chunks_document_id_fkey"
            columns: ["document_id"]
            isOneToOne: false
            referencedRelation: "documents"
            referencedColumns: ["id"]
          },
        ]
      }
      documents: {
        Row: {
          created_at: string
          doc_type: string
          id: number
          language: string
          source: string | null
          storage_path: string | null
          title: string
        }
        Insert: {
          created_at?: string
          doc_type: string
          id?: never
          language?: string
          source?: string | null
          storage_path?: string | null
          title: string
        }
        Update: {
          created_at?: string
          doc_type?: string
          id?: never
          language?: string
          source?: string | null
          storage_path?: string | null
          title?: string
        }
        Relationships: []
      }
      fatigue_log: {
        Row: {
          ear_avg: number | null
          fatigue_level: Database["public"]["Enums"]["fatigue_level"]
          fatigue_score: number | null
          head_down_events: number | null
          id: number
          operator_id: string
          perclos_60s: number | null
          phone_detected: boolean | null
          shift_id: string | null
          ts: string
          yawn_count: number | null
        }
        Insert: {
          ear_avg?: number | null
          fatigue_level: Database["public"]["Enums"]["fatigue_level"]
          fatigue_score?: number | null
          head_down_events?: number | null
          id?: never
          operator_id: string
          perclos_60s?: number | null
          phone_detected?: boolean | null
          shift_id?: string | null
          ts: string
          yawn_count?: number | null
        }
        Update: {
          ear_avg?: number | null
          fatigue_level?: Database["public"]["Enums"]["fatigue_level"]
          fatigue_score?: number | null
          head_down_events?: number | null
          id?: never
          operator_id?: string
          perclos_60s?: number | null
          phone_detected?: boolean | null
          shift_id?: string | null
          ts?: string
          yawn_count?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "fatigue_log_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
          {
            foreignKeyName: "fatigue_log_shift_id_fkey"
            columns: ["shift_id"]
            isOneToOne: false
            referencedRelation: "shifts"
            referencedColumns: ["shift_id"]
          },
        ]
      }
      fleet_metrics_weekly: {
        Row: {
          anomaly_count: number | null
          cluster_id: number | null
          cluster_label: string | null
          efficiency_index: number | null
          entity_id: string
          entity_type: Database["public"]["Enums"]["metric_entity"]
          fuel_per_productive_hour: number | null
          id: number
          idle_pct: number | null
          is_outlier: boolean | null
          outlier_reason: string | null
          productive_hours: number | null
          productivity_per_hour: number | null
          rank_in_site: number | null
          safety_event_count: number | null
          site_id: string
          time_ratio: number | null
          verified_at: string | null
          verified_by: string | null
          week_start: string
        }
        Insert: {
          anomaly_count?: number | null
          cluster_id?: number | null
          cluster_label?: string | null
          efficiency_index?: number | null
          entity_id: string
          entity_type: Database["public"]["Enums"]["metric_entity"]
          fuel_per_productive_hour?: number | null
          id?: never
          idle_pct?: number | null
          is_outlier?: boolean | null
          outlier_reason?: string | null
          productive_hours?: number | null
          productivity_per_hour?: number | null
          rank_in_site?: number | null
          safety_event_count?: number | null
          site_id: string
          time_ratio?: number | null
          verified_at?: string | null
          verified_by?: string | null
          week_start: string
        }
        Update: {
          anomaly_count?: number | null
          cluster_id?: number | null
          cluster_label?: string | null
          efficiency_index?: number | null
          entity_id?: string
          entity_type?: Database["public"]["Enums"]["metric_entity"]
          fuel_per_productive_hour?: number | null
          id?: never
          idle_pct?: number | null
          is_outlier?: boolean | null
          outlier_reason?: string | null
          productive_hours?: number | null
          productivity_per_hour?: number | null
          rank_in_site?: number | null
          safety_event_count?: number | null
          site_id?: string
          time_ratio?: number | null
          verified_at?: string | null
          verified_by?: string | null
          week_start?: string
        }
        Relationships: [
          {
            foreignKeyName: "fleet_metrics_weekly_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
          {
            foreignKeyName: "fleet_metrics_weekly_verified_by_fkey"
            columns: ["verified_by"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      geofences: {
        Row: {
          active: boolean
          created_at: string
          created_by: string | null
          id: number
          name: string
          polygon: Json
          site_id: string
          speed_limit_kmh: number | null
          zone_type: Database["public"]["Enums"]["geofence_type"]
        }
        Insert: {
          active?: boolean
          created_at?: string
          created_by?: string | null
          id?: never
          name: string
          polygon: Json
          site_id: string
          speed_limit_kmh?: number | null
          zone_type: Database["public"]["Enums"]["geofence_type"]
        }
        Update: {
          active?: boolean
          created_at?: string
          created_by?: string | null
          id?: never
          name?: string
          polygon?: Json
          site_id?: string
          speed_limit_kmh?: number | null
          zone_type?: Database["public"]["Enums"]["geofence_type"]
        }
        Relationships: [
          {
            foreignKeyName: "geofences_created_by_fkey"
            columns: ["created_by"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "geofences_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
      incidents: {
        Row: {
          ai_summary: string | null
          client_id: string
          created_at: string
          created_by: string | null
          damage_description: string | null
          description: string
          id: number
          incident_type: Database["public"]["Enums"]["incident_type"]
          injury: boolean
          linked_alert_id: number | null
          linked_event_id: number | null
          machine_id: string | null
          media_paths: string[]
          operator_id: string | null
          reported_via: Database["public"]["Enums"]["report_channel"]
          root_cause: string | null
          severity: Database["public"]["Enums"]["severity_level"]
          site_id: string
          status: Database["public"]["Enums"]["incident_status"]
          ts: string
          voice_transcript: string | null
        }
        Insert: {
          ai_summary?: string | null
          client_id: string
          created_at?: string
          created_by?: string | null
          damage_description?: string | null
          description: string
          id?: never
          incident_type: Database["public"]["Enums"]["incident_type"]
          injury?: boolean
          linked_alert_id?: number | null
          linked_event_id?: number | null
          machine_id?: string | null
          media_paths?: string[]
          operator_id?: string | null
          reported_via?: Database["public"]["Enums"]["report_channel"]
          root_cause?: string | null
          severity: Database["public"]["Enums"]["severity_level"]
          site_id: string
          status?: Database["public"]["Enums"]["incident_status"]
          ts: string
          voice_transcript?: string | null
        }
        Update: {
          ai_summary?: string | null
          client_id?: string
          created_at?: string
          created_by?: string | null
          damage_description?: string | null
          description?: string
          id?: never
          incident_type?: Database["public"]["Enums"]["incident_type"]
          injury?: boolean
          linked_alert_id?: number | null
          linked_event_id?: number | null
          machine_id?: string | null
          media_paths?: string[]
          operator_id?: string | null
          reported_via?: Database["public"]["Enums"]["report_channel"]
          root_cause?: string | null
          severity?: Database["public"]["Enums"]["severity_level"]
          site_id?: string
          status?: Database["public"]["Enums"]["incident_status"]
          ts?: string
          voice_transcript?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "incidents_created_by_fkey"
            columns: ["created_by"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "incidents_linked_alert_id_fkey"
            columns: ["linked_alert_id"]
            isOneToOne: false
            referencedRelation: "alerts"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "incidents_linked_event_id_fkey"
            columns: ["linked_event_id"]
            isOneToOne: false
            referencedRelation: "safety_events"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "incidents_machine_id_fkey"
            columns: ["machine_id"]
            isOneToOne: false
            referencedRelation: "machines"
            referencedColumns: ["machine_id"]
          },
          {
            foreignKeyName: "incidents_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
          {
            foreignKeyName: "incidents_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
      machine_health_snapshots: {
        Row: {
          anomaly_score: number | null
          cooling_score: number | null
          details: Json | null
          electrical_score: number | null
          engine_score: number | null
          hydraulics_score: number | null
          id: number
          machine_id: string
          overall_score: number
          ts: string
          undercarriage_score: number | null
        }
        Insert: {
          anomaly_score?: number | null
          cooling_score?: number | null
          details?: Json | null
          electrical_score?: number | null
          engine_score?: number | null
          hydraulics_score?: number | null
          id?: never
          machine_id: string
          overall_score: number
          ts?: string
          undercarriage_score?: number | null
        }
        Update: {
          anomaly_score?: number | null
          cooling_score?: number | null
          details?: Json | null
          electrical_score?: number | null
          engine_score?: number | null
          hydraulics_score?: number | null
          id?: never
          machine_id?: string
          overall_score?: number
          ts?: string
          undercarriage_score?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "machine_health_snapshots_machine_id_fkey"
            columns: ["machine_id"]
            isOneToOne: false
            referencedRelation: "machines"
            referencedColumns: ["machine_id"]
          },
        ]
      }
      machines: {
        Row: {
          created_at: string
          health_score: number
          hours_since_service: number
          machine_id: string
          machine_type: Database["public"]["Enums"]["machine_type"]
          model: string
          serial_no: string | null
          service_interval_hours: number
          site_id: string
          status: Database["public"]["Enums"]["machine_status"]
          total_engine_hours: number
          year: number | null
        }
        Insert: {
          created_at?: string
          health_score?: number
          hours_since_service?: number
          machine_id: string
          machine_type: Database["public"]["Enums"]["machine_type"]
          model: string
          serial_no?: string | null
          service_interval_hours?: number
          site_id: string
          status?: Database["public"]["Enums"]["machine_status"]
          total_engine_hours?: number
          year?: number | null
        }
        Update: {
          created_at?: string
          health_score?: number
          hours_since_service?: number
          machine_id?: string
          machine_type?: Database["public"]["Enums"]["machine_type"]
          model?: string
          serial_no?: string | null
          service_interval_hours?: number
          site_id?: string
          status?: Database["public"]["Enums"]["machine_status"]
          total_engine_hours?: number
          year?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "machines_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
      maintenance_log: {
        Row: {
          component: Database["public"]["Enums"]["machine_component"]
          cost_inr: number | null
          downtime_hours: number | null
          engine_hours_at_event: number
          event_date: string
          event_type: Database["public"]["Enums"]["maintenance_event"]
          id: number
          machine_id: string
          notes: string | null
        }
        Insert: {
          component: Database["public"]["Enums"]["machine_component"]
          cost_inr?: number | null
          downtime_hours?: number | null
          engine_hours_at_event: number
          event_date: string
          event_type: Database["public"]["Enums"]["maintenance_event"]
          id?: never
          machine_id: string
          notes?: string | null
        }
        Update: {
          component?: Database["public"]["Enums"]["machine_component"]
          cost_inr?: number | null
          downtime_hours?: number | null
          engine_hours_at_event?: number
          event_date?: string
          event_type?: Database["public"]["Enums"]["maintenance_event"]
          id?: never
          machine_id?: string
          notes?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "maintenance_log_machine_id_fkey"
            columns: ["machine_id"]
            isOneToOne: false
            referencedRelation: "machines"
            referencedColumns: ["machine_id"]
          },
        ]
      }
      maintenance_predictions: {
        Row: {
          failure_probability: number
          horizon_hours: number
          id: number
          likely_component: Database["public"]["Enums"]["machine_component"] | null
          machine_id: string
          model_version: string
          predicted_at: string
          top_factors: Json | null
        }
        Insert: {
          failure_probability: number
          horizon_hours?: number
          id?: never
          likely_component?: Database["public"]["Enums"]["machine_component"] | null
          machine_id: string
          model_version: string
          predicted_at?: string
          top_factors?: Json | null
        }
        Update: {
          failure_probability?: number
          horizon_hours?: number
          id?: never
          likely_component?: Database["public"]["Enums"]["machine_component"] | null
          machine_id?: string
          model_version?: string
          predicted_at?: string
          top_factors?: Json | null
        }
        Relationships: [
          {
            foreignKeyName: "maintenance_predictions_machine_id_fkey"
            columns: ["machine_id"]
            isOneToOne: false
            referencedRelation: "machines"
            referencedColumns: ["machine_id"]
          },
        ]
      }
      model_runs: {
        Row: {
          artifact_path: string | null
          id: number
          is_active: boolean
          metrics: Json | null
          model_name: string
          params: Json | null
          trained_at: string
          version: string
        }
        Insert: {
          artifact_path?: string | null
          id?: never
          is_active?: boolean
          metrics?: Json | null
          model_name: string
          params?: Json | null
          trained_at?: string
          version: string
        }
        Update: {
          artifact_path?: string | null
          id?: never
          is_active?: boolean
          metrics?: Json | null
          model_name?: string
          params?: Json | null
          trained_at?: string
          version?: string
        }
        Relationships: []
      }
      operators: {
        Row: {
          certification_level: number
          created_at: string
          experience_years: number
          full_name: string
          languages: string[]
          operator_id: string
          personality: string | null
          preferred_shift: Database["public"]["Enums"]["shift_type"]
          site_id: string
          skill_score: number
        }
        Insert: {
          certification_level: number
          created_at?: string
          experience_years: number
          full_name: string
          languages?: string[]
          operator_id: string
          personality?: string | null
          preferred_shift?: Database["public"]["Enums"]["shift_type"]
          site_id: string
          skill_score: number
        }
        Update: {
          certification_level?: number
          created_at?: string
          experience_years?: number
          full_name?: string
          languages?: string[]
          operator_id?: string
          personality?: string | null
          preferred_shift?: Database["public"]["Enums"]["shift_type"]
          site_id?: string
          skill_score?: number
        }
        Relationships: [
          {
            foreignKeyName: "operators_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
      profiles: {
        Row: {
          created_at: string
          full_name: string | null
          id: string
          operator_id: string | null
          preferred_language: string
          role: Database["public"]["Enums"]["user_role"]
          site_id: string | null
        }
        Insert: {
          created_at?: string
          full_name?: string | null
          id: string
          operator_id?: string | null
          preferred_language?: string
          role?: Database["public"]["Enums"]["user_role"]
          site_id?: string | null
        }
        Update: {
          created_at?: string
          full_name?: string | null
          id?: string
          operator_id?: string | null
          preferred_language?: string
          role?: Database["public"]["Enums"]["user_role"]
          site_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "profiles_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
          {
            foreignKeyName: "profiles_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
      safety_events: {
        Row: {
          alert_id: number | null
          approaching: boolean | null
          details: Json | null
          distance_m: number | null
          event_type: Database["public"]["Enums"]["safety_event_type"]
          id: number
          machine_id: string | null
          operator_id: string | null
          resolved: boolean
          sector: Database["public"]["Enums"]["camera_sector"] | null
          severity: Database["public"]["Enums"]["severity_level"]
          site_id: string
          ts: string
        }
        Insert: {
          alert_id?: number | null
          approaching?: boolean | null
          details?: Json | null
          distance_m?: number | null
          event_type: Database["public"]["Enums"]["safety_event_type"]
          id?: never
          machine_id?: string | null
          operator_id?: string | null
          resolved?: boolean
          sector?: Database["public"]["Enums"]["camera_sector"] | null
          severity: Database["public"]["Enums"]["severity_level"]
          site_id: string
          ts?: string
        }
        Update: {
          alert_id?: number | null
          approaching?: boolean | null
          details?: Json | null
          distance_m?: number | null
          event_type?: Database["public"]["Enums"]["safety_event_type"]
          id?: never
          machine_id?: string | null
          operator_id?: string | null
          resolved?: boolean
          sector?: Database["public"]["Enums"]["camera_sector"] | null
          severity?: Database["public"]["Enums"]["severity_level"]
          site_id?: string
          ts?: string
        }
        Relationships: [
          {
            foreignKeyName: "safety_events_alert_id_fkey"
            columns: ["alert_id"]
            isOneToOne: false
            referencedRelation: "alerts"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "safety_events_machine_id_fkey"
            columns: ["machine_id"]
            isOneToOne: false
            referencedRelation: "machines"
            referencedColumns: ["machine_id"]
          },
          {
            foreignKeyName: "safety_events_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
          {
            foreignKeyName: "safety_events_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
      session_bookings: {
        Row: {
          created_at: string
          id: number
          operator_id: string
          session_id: number
          status: string
        }
        Insert: {
          created_at?: string
          id?: never
          operator_id: string
          session_id: number
          status?: string
        }
        Update: {
          created_at?: string
          id?: never
          operator_id?: string
          session_id?: number
          status?: string
        }
        Relationships: [
          {
            foreignKeyName: "session_bookings_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
          {
            foreignKeyName: "session_bookings_session_id_fkey"
            columns: ["session_id"]
            isOneToOne: false
            referencedRelation: "training_sessions"
            referencedColumns: ["id"]
          },
        ]
      }
      shifts: {
        Row: {
          end_time: string
          fuel_end_pct: number | null
          fuel_start_pct: number | null
          handover_generated_at: string | null
          handover_notes: string | null
          handover_summary: string | null
          issues_reported: string[] | null
          machine_id: string
          operator_id: string
          shift_date: string
          shift_id: string
          shift_type: Database["public"]["Enums"]["shift_type"]
          site_id: string
          start_time: string
        }
        Insert: {
          end_time: string
          fuel_end_pct?: number | null
          fuel_start_pct?: number | null
          handover_generated_at?: string | null
          handover_notes?: string | null
          handover_summary?: string | null
          issues_reported?: string[] | null
          machine_id: string
          operator_id: string
          shift_date: string
          shift_id: string
          shift_type: Database["public"]["Enums"]["shift_type"]
          site_id: string
          start_time: string
        }
        Update: {
          end_time?: string
          fuel_end_pct?: number | null
          fuel_start_pct?: number | null
          handover_generated_at?: string | null
          handover_notes?: string | null
          handover_summary?: string | null
          issues_reported?: string[] | null
          machine_id?: string
          operator_id?: string
          shift_date?: string
          shift_id?: string
          shift_type?: Database["public"]["Enums"]["shift_type"]
          site_id?: string
          start_time?: string
        }
        Relationships: [
          {
            foreignKeyName: "shifts_machine_id_fkey"
            columns: ["machine_id"]
            isOneToOne: false
            referencedRelation: "machines"
            referencedColumns: ["machine_id"]
          },
          {
            foreignKeyName: "shifts_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
          {
            foreignKeyName: "shifts_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
      sites: {
        Row: {
          created_at: string
          lat: number
          lon: number
          name: string
          site_id: string
          site_type: string
          timezone: string
        }
        Insert: {
          created_at?: string
          lat: number
          lon: number
          name: string
          site_id: string
          site_type: string
          timezone?: string
        }
        Update: {
          created_at?: string
          lat?: number
          lon?: number
          name?: string
          site_id?: string
          site_type?: string
          timezone?: string
        }
        Relationships: []
      }
      tasks: {
        Row: {
          actual_duration_min: number | null
          actual_end: string | null
          actual_start: string | null
          delay_reason: string | null
          haul_distance_m: number | null
          machine_id: string
          material_type: Database["public"]["Enums"]["material_type"]
          operator_id: string
          predicted_p10_min: number | null
          predicted_p50_min: number | null
          predicted_p90_min: number | null
          prediction_factors: Json | null
          priority: number
          quantity: number
          scheduled_start: string | null
          sequence_no: number
          shift_id: string
          site_id: string
          status: Database["public"]["Enums"]["task_status"]
          task_date: string
          task_id: string
          task_type: Database["public"]["Enums"]["task_type"]
          terrain_slope_deg: number
          unit: string
          updated_at: string
        }
        Insert: {
          actual_duration_min?: number | null
          actual_end?: string | null
          actual_start?: string | null
          delay_reason?: string | null
          haul_distance_m?: number | null
          machine_id: string
          material_type: Database["public"]["Enums"]["material_type"]
          operator_id: string
          predicted_p10_min?: number | null
          predicted_p50_min?: number | null
          predicted_p90_min?: number | null
          prediction_factors?: Json | null
          priority?: number
          quantity: number
          scheduled_start?: string | null
          sequence_no: number
          shift_id: string
          site_id: string
          status?: Database["public"]["Enums"]["task_status"]
          task_date: string
          task_id: string
          task_type: Database["public"]["Enums"]["task_type"]
          terrain_slope_deg?: number
          unit: string
          updated_at?: string
        }
        Update: {
          actual_duration_min?: number | null
          actual_end?: string | null
          actual_start?: string | null
          delay_reason?: string | null
          haul_distance_m?: number | null
          machine_id?: string
          material_type?: Database["public"]["Enums"]["material_type"]
          operator_id?: string
          predicted_p10_min?: number | null
          predicted_p50_min?: number | null
          predicted_p90_min?: number | null
          prediction_factors?: Json | null
          priority?: number
          quantity?: number
          scheduled_start?: string | null
          sequence_no?: number
          shift_id?: string
          site_id?: string
          status?: Database["public"]["Enums"]["task_status"]
          task_date?: string
          task_id?: string
          task_type?: Database["public"]["Enums"]["task_type"]
          terrain_slope_deg?: number
          unit?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "tasks_machine_id_fkey"
            columns: ["machine_id"]
            isOneToOne: false
            referencedRelation: "machines"
            referencedColumns: ["machine_id"]
          },
          {
            foreignKeyName: "tasks_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
          {
            foreignKeyName: "tasks_shift_id_fkey"
            columns: ["shift_id"]
            isOneToOne: false
            referencedRelation: "shifts"
            referencedColumns: ["shift_id"]
          },
          {
            foreignKeyName: "tasks_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
      telemetry: {
        Row: {
          anomaly_label: boolean
          anomaly_type: string | null
          battery_voltage: number | null
          coolant_temp_c: number | null
          engine_load_pct: number | null
          engine_oil_temp_c: number | null
          engine_rpm: number | null
          fault_code: string | null
          fuel_level_pct: number | null
          fuel_rate_lph: number | null
          gps_lat: number | null
          gps_lon: number | null
          ground_speed_kmh: number | null
          hydraulic_oil_temp_c: number | null
          hydraulic_pressure_bar: number | null
          id: number
          is_idle: boolean | null
          machine_id: string
          oil_pressure_kpa: number | null
          operator_id: string | null
          pitch_deg: number | null
          roll_deg: number | null
          seatbelt_fastened: boolean | null
          shift_id: string | null
          ts: string
          vibration_rms_g: number | null
        }
        Insert: {
          anomaly_label?: boolean
          anomaly_type?: string | null
          battery_voltage?: number | null
          coolant_temp_c?: number | null
          engine_load_pct?: number | null
          engine_oil_temp_c?: number | null
          engine_rpm?: number | null
          fault_code?: string | null
          fuel_level_pct?: number | null
          fuel_rate_lph?: number | null
          gps_lat?: number | null
          gps_lon?: number | null
          ground_speed_kmh?: number | null
          hydraulic_oil_temp_c?: number | null
          hydraulic_pressure_bar?: number | null
          id?: never
          is_idle?: boolean | null
          machine_id: string
          oil_pressure_kpa?: number | null
          operator_id?: string | null
          pitch_deg?: number | null
          roll_deg?: number | null
          seatbelt_fastened?: boolean | null
          shift_id?: string | null
          ts: string
          vibration_rms_g?: number | null
        }
        Update: {
          anomaly_label?: boolean
          anomaly_type?: string | null
          battery_voltage?: number | null
          coolant_temp_c?: number | null
          engine_load_pct?: number | null
          engine_oil_temp_c?: number | null
          engine_rpm?: number | null
          fault_code?: string | null
          fuel_level_pct?: number | null
          fuel_rate_lph?: number | null
          gps_lat?: number | null
          gps_lon?: number | null
          ground_speed_kmh?: number | null
          hydraulic_oil_temp_c?: number | null
          hydraulic_pressure_bar?: number | null
          id?: never
          is_idle?: boolean | null
          machine_id?: string
          oil_pressure_kpa?: number | null
          operator_id?: string | null
          pitch_deg?: number | null
          roll_deg?: number | null
          seatbelt_fastened?: boolean | null
          shift_id?: string | null
          ts?: string
          vibration_rms_g?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "telemetry_machine_id_fkey"
            columns: ["machine_id"]
            isOneToOne: false
            referencedRelation: "machines"
            referencedColumns: ["machine_id"]
          },
          {
            foreignKeyName: "telemetry_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
          {
            foreignKeyName: "telemetry_shift_id_fkey"
            columns: ["shift_id"]
            isOneToOne: false
            referencedRelation: "shifts"
            referencedColumns: ["shift_id"]
          },
        ]
      }
      training_modules: {
        Row: {
          content_path: string | null
          difficulty: number | null
          duration_min: number | null
          format: Database["public"]["Enums"]["training_format"]
          languages: string[]
          machine_types: Database["public"]["Enums"]["machine_type"][] | null
          module_id: string
          scenario: Json | null
          target_metric: string | null
          title: string
          topic: string
        }
        Insert: {
          content_path?: string | null
          difficulty?: number | null
          duration_min?: number | null
          format: Database["public"]["Enums"]["training_format"]
          languages?: string[]
          machine_types?: Database["public"]["Enums"]["machine_type"][] | null
          module_id: string
          scenario?: Json | null
          target_metric?: string | null
          title: string
          topic: string
        }
        Update: {
          content_path?: string | null
          difficulty?: number | null
          duration_min?: number | null
          format?: Database["public"]["Enums"]["training_format"]
          languages?: string[]
          machine_types?: Database["public"]["Enums"]["machine_type"][] | null
          module_id?: string
          scenario?: Json | null
          target_metric?: string | null
          title?: string
          topic?: string
        }
        Relationships: []
      }
      training_recommendations: {
        Row: {
          created_at: string
          id: number
          module_id: string
          operator_id: string
          reason: string
          status: Database["public"]["Enums"]["recommendation_status"]
          trigger_metric: string | null
          trigger_value: number | null
        }
        Insert: {
          created_at?: string
          id?: never
          module_id: string
          operator_id: string
          reason: string
          status?: Database["public"]["Enums"]["recommendation_status"]
          trigger_metric?: string | null
          trigger_value?: number | null
        }
        Update: {
          created_at?: string
          id?: never
          module_id?: string
          operator_id?: string
          reason?: string
          status?: Database["public"]["Enums"]["recommendation_status"]
          trigger_metric?: string | null
          trigger_value?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "training_recommendations_module_id_fkey"
            columns: ["module_id"]
            isOneToOne: false
            referencedRelation: "training_modules"
            referencedColumns: ["module_id"]
          },
          {
            foreignKeyName: "training_recommendations_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
        ]
      }
      training_records: {
        Row: {
          client_id: string
          completed_at: string | null
          id: number
          module_id: string
          operator_id: string
          passed: boolean | null
          score: number | null
          started_at: string
        }
        Insert: {
          client_id: string
          completed_at?: string | null
          id?: never
          module_id: string
          operator_id: string
          passed?: boolean | null
          score?: number | null
          started_at?: string
        }
        Update: {
          client_id?: string
          completed_at?: string | null
          id?: never
          module_id?: string
          operator_id?: string
          passed?: boolean | null
          score?: number | null
          started_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "training_records_module_id_fkey"
            columns: ["module_id"]
            isOneToOne: false
            referencedRelation: "training_modules"
            referencedColumns: ["module_id"]
          },
          {
            foreignKeyName: "training_records_operator_id_fkey"
            columns: ["operator_id"]
            isOneToOne: false
            referencedRelation: "operators"
            referencedColumns: ["operator_id"]
          },
        ]
      }
      training_sessions: {
        Row: {
          capacity: number
          duration_min: number
          id: number
          instructor_name: string
          module_id: string | null
          site_id: string
          starts_at: string
        }
        Insert: {
          capacity?: number
          duration_min?: number
          id?: never
          instructor_name: string
          module_id?: string | null
          site_id: string
          starts_at: string
        }
        Update: {
          capacity?: number
          duration_min?: number
          id?: never
          instructor_name?: string
          module_id?: string | null
          site_id?: string
          starts_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "training_sessions_module_id_fkey"
            columns: ["module_id"]
            isOneToOne: false
            referencedRelation: "training_modules"
            referencedColumns: ["module_id"]
          },
          {
            foreignKeyName: "training_sessions_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
      weather: {
        Row: {
          dust_index: number | null
          humidity_pct: number | null
          id: number
          rain_mm: number | null
          site_id: string
          temp_c: number | null
          ts: string
          visibility_m: number | null
          wind_kmh: number | null
        }
        Insert: {
          dust_index?: number | null
          humidity_pct?: number | null
          id?: never
          rain_mm?: number | null
          site_id: string
          temp_c?: number | null
          ts: string
          visibility_m?: number | null
          wind_kmh?: number | null
        }
        Update: {
          dust_index?: number | null
          humidity_pct?: number | null
          id?: never
          rain_mm?: number | null
          site_id?: string
          temp_c?: number | null
          ts?: string
          visibility_m?: number | null
          wind_kmh?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "weather_site_id_fkey"
            columns: ["site_id"]
            isOneToOne: false
            referencedRelation: "sites"
            referencedColumns: ["site_id"]
          },
        ]
      }
    }
    Views: {
      v_machine_health_latest: {
        Row: {
          anomaly_score: number | null
          cooling_score: number | null
          details: Json | null
          electrical_score: number | null
          engine_score: number | null
          hydraulics_score: number | null
          id: number | null
          machine_id: string | null
          overall_score: number | null
          ts: string | null
          undercarriage_score: number | null
        }
        Relationships: []
      }
      v_machine_latest: {
        Row: {
          anomaly_label: boolean | null
          anomaly_type: string | null
          battery_voltage: number | null
          coolant_temp_c: number | null
          engine_load_pct: number | null
          engine_oil_temp_c: number | null
          engine_rpm: number | null
          fault_code: string | null
          fuel_level_pct: number | null
          fuel_rate_lph: number | null
          gps_lat: number | null
          gps_lon: number | null
          ground_speed_kmh: number | null
          hydraulic_oil_temp_c: number | null
          hydraulic_pressure_bar: number | null
          id: number | null
          is_idle: boolean | null
          machine_id: string | null
          oil_pressure_kpa: number | null
          operator_id: string | null
          pitch_deg: number | null
          roll_deg: number | null
          seatbelt_fastened: boolean | null
          shift_id: string | null
          ts: string | null
          vibration_rms_g: number | null
        }
        Relationships: []
      }
      v_open_alerts: {
        Row: {
          acknowledged_at: string | null
          acknowledged_by: string | null
          alert_code: string | null
          anomaly_score: number | null
          category: Database["public"]["Enums"]["alert_category"] | null
          evidence: Json | null
          id: number | null
          machine_id: string | null
          message: string | null
          operator_id: string | null
          recommended_action: string | null
          resolved_at: string | null
          severity: Database["public"]["Enums"]["severity_level"] | null
          site_id: string | null
          source: Database["public"]["Enums"]["alert_source"] | null
          stage: Database["public"]["Enums"]["alert_stage"] | null
          title: string | null
          ts: string | null
        }
        Relationships: []
      }
    }
    Functions: {
      auth_operator_id: {
        Args: never
        Returns: string
      }
      auth_role: {
        Args: never
        Returns: Database["public"]["Enums"]["user_role"]
      }
      is_manager: {
        Args: never
        Returns: boolean
      }
      match_document_chunks: {
        Args: {
          query_embedding: string
          match_count: number
          min_similarity: number
        }
        Returns: {
          id: number
          document_id: number
          content: string
          metadata: Json
          similarity: number
        }[]
      }
    }
    Enums: {
      alert_category: "internal" | "safety" | "maintenance" | "behaviour" | "emergency"
      alert_source: "rule" | "anomaly_model" | "predictive_model" | "vision" | "operator" | "system"
      alert_stage: "warn" | "derate" | "recommend_shutdown" | "escalated" | "resolved"
      camera_sector: "front" | "rear" | "left" | "right" | "cab"
      fatigue_level: "low" | "medium" | "high"
      geofence_type: "no_go" | "pedestrian" | "power_line" | "trench" | "speed_limited"
      incident_status: "open" | "investigating" | "closed"
      incident_type: "near_miss" | "collision" | "injury" | "equipment_damage" | "spill_leak" | "other"
      machine_component: "engine" | "hydraulics" | "cooling" | "electrical" | "brakes" | "undercarriage" | "other"
      machine_status: "active" | "idle" | "maintenance" | "down"
      machine_type: "excavator" | "wheel_loader" | "dozer" | "articulated_truck"
      maintenance_event: "scheduled_service" | "inspection" | "repair" | "failure"
      material_type: "clay" | "sand" | "gravel" | "rock" | "topsoil"
      metric_entity: "operator" | "machine"
      recommendation_status: "pending" | "accepted" | "dismissed" | "completed"
      report_channel: "form" | "voice" | "auto"
      safety_event_type: "seatbelt_unfastened" | "proximity_breach" | "blindspot_intrusion" | "fatigue_high" | "phone_use" | "tip_risk" | "geofence_breach" | "harsh_maneuver" | "overspeed" | "sos"
      severity_level: "info" | "warning" | "critical" | "emergency"
      shift_type: "day" | "night"
      task_status: "scheduled" | "in_progress" | "completed" | "delayed" | "cancelled"
      task_type: "dig" | "trench" | "load" | "haul" | "grade" | "backfill"
      training_format: "video" | "document" | "quiz" | "scenario" | "instructor_session"
      user_role: "operator" | "manager" | "admin"
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {
      alert_category: ["internal", "safety", "maintenance", "behaviour", "emergency"],
      alert_source: ["rule", "anomaly_model", "predictive_model", "vision", "operator", "system"],
      alert_stage: ["warn", "derate", "recommend_shutdown", "escalated", "resolved"],
      camera_sector: ["front", "rear", "left", "right", "cab"],
      fatigue_level: ["low", "medium", "high"],
      geofence_type: ["no_go", "pedestrian", "power_line", "trench", "speed_limited"],
      incident_status: ["open", "investigating", "closed"],
      incident_type: ["near_miss", "collision", "injury", "equipment_damage", "spill_leak", "other"],
      machine_component: ["engine", "hydraulics", "cooling", "electrical", "brakes", "undercarriage", "other"],
      machine_status: ["active", "idle", "maintenance", "down"],
      machine_type: ["excavator", "wheel_loader", "dozer", "articulated_truck"],
      maintenance_event: ["scheduled_service", "inspection", "repair", "failure"],
      material_type: ["clay", "sand", "gravel", "rock", "topsoil"],
      metric_entity: ["operator", "machine"],
      recommendation_status: ["pending", "accepted", "dismissed", "completed"],
      report_channel: ["form", "voice", "auto"],
      safety_event_type: ["seatbelt_unfastened", "proximity_breach", "blindspot_intrusion", "fatigue_high", "phone_use", "tip_risk", "geofence_breach", "harsh_maneuver", "overspeed", "sos"],
      severity_level: ["info", "warning", "critical", "emergency"],
      shift_type: ["day", "night"],
      task_status: ["scheduled", "in_progress", "completed", "delayed", "cancelled"],
      task_type: ["dig", "trench", "load", "haul", "grade", "backfill"],
      training_format: ["video", "document", "quiz", "scenario", "instructor_session"],
      user_role: ["operator", "manager", "admin"],
    },
  },
} as const
