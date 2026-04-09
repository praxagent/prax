{{/*
Expand the name of the chart.
*/}}
{{- define "prax.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this.
*/}}
{{- define "prax.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "prax.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "prax.labels" -}}
helm.sh/chart: {{ include "prax.chart" . }}
{{ include "prax.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — used for matchLabels in deployments/services.
*/}}
{{- define "prax.selectorLabels" -}}
app.kubernetes.io/name: {{ include "prax.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Component-specific labels. Call with (dict "component" "app" "context" $)
*/}}
{{- define "prax.componentLabels" -}}
{{ include "prax.labels" .context }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Component-specific selector labels.
*/}}
{{- define "prax.componentSelectorLabels" -}}
{{ include "prax.selectorLabels" .context }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Service account name.
*/}}
{{- define "prax.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "prax.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Image reference helper. Call with (dict "img" .Values.images.app)
*/}}
{{- define "prax.image" -}}
{{- if .img.registry }}
{{- printf "%s/%s:%s" .img.registry .img.repository .img.tag }}
{{- else }}
{{- printf "%s:%s" .img.repository .img.tag }}
{{- end }}
{{- end }}

{{/*
Namespace — uses the release namespace.
*/}}
{{- define "prax.namespace" -}}
{{ .Release.Namespace }}
{{- end }}
