{{/*
  aifeeders.fullname — release-scoped name, capped at 63 chars
*/}}
{{- define "aifeeders.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
  aifeeders.labels — standard labels applied to every resource
*/}}
{{- define "aifeeders.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
  aifeeders.selectorLabels — minimal label set used in selectors (immutable once deployed)
*/}}
{{- define "aifeeders.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
  aifeeders.image — build a full image reference from registry + name + tag
  Usage: {{ include "aifeeders.image" (dict "registry" .Values.global.image.registry "name" .Values.api.image "tag" .Values.global.image.tag) }}
*/}}
{{- define "aifeeders.image" -}}
{{- if .registry -}}
{{- printf "%s/%s:%s" .registry .name .tag -}}
{{- else -}}
{{- printf "%s:%s" .name .tag -}}
{{- end -}}
{{- end -}}
