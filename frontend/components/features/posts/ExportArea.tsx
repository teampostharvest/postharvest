"use client";

import { useState } from "react";
import { FileJson, FileSpreadsheet, Loader2, RotateCcw, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { ExportFormat, JobStatus } from "@/lib/types";
import { EXPORT_FILENAMES } from "@/lib/types";

export interface ExportAreaProps {
  jobId: string | null;
  status: JobStatus | null;
  onNewScrape: () => void;
}

const FORMATS: Array<{
  format: ExportFormat;
  title: string;
  description: string;
  icon: typeof FileJson;
}> = [
  { format: "json", title: "JSON", description: "Complete nested structure", icon: FileJson },
  { format: "csv", title: "CSV", description: "Flattened spreadsheet", icon: FileText },
  { format: "excel", title: "Excel", description: "XLSX workbook, multiple sheets", icon: FileSpreadsheet },
];

export function ExportArea({ jobId, status, onNewScrape }: ExportAreaProps) {
  const [downloading, setDownloading] = useState<ExportFormat | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!jobId || (status !== "completed" && status !== "failed")) return null;

  const handleDownload = async (format: ExportFormat) => {
    setError(null);
    setDownloading(format);
    try {
      await api.exportJobDownload(jobId, format);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed. Please try again.");
    } finally {
      setDownloading(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Export results</CardTitle>
        <CardDescription>
          Downloads are generated live by the backend from the stored job results.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {FORMATS.map((entry) => {
            const busy = downloading === entry.format;
            return (
              <button
                key={entry.format}
                type="button"
                onClick={() => void handleDownload(entry.format)}
                disabled={downloading !== null}
                className="group rounded-lg border bg-card p-4 text-left transition-colors hover:bg-bg-subtle/60 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <div className="flex items-center gap-3">
                  {busy ? (
                    <Loader2 className="h-6 w-6 animate-spin text-ink" aria-hidden="true" />
                  ) : (
                    <entry.icon className="h-6 w-6 text-ink" aria-hidden="true" />
                  )}
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">{busy ? "Preparing…" : `↓ ${entry.title}`}</p>
                    <p className="truncate text-xs text-ink-muted">{entry.description}</p>
                  </div>
                </div>
                <p className="mt-3 truncate font-mono text-xs text-ink-muted">
                  {EXPORT_FILENAMES[entry.format]}
                </p>
              </button>
            );
          })}
        </div>

        {error ? (
          <p className="flex items-start gap-2 border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
            {error}
          </p>
        ) : null}

        <div className="flex flex-col-reverse items-start justify-between gap-3 border-t pt-4 sm:flex-row sm:items-center">
          <p className="text-xs text-ink-muted">
            Job <code className="rounded bg-bg-subtle px-1.5 py-0.5 font-mono">{jobId}</code>
          </p>
          <Button type="button" variant="outline" onClick={onNewScrape}>
            <RotateCcw className="h-4 w-4" aria-hidden="true" /> New scrape
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}