"use client";

import { useState } from "react";

// Default config matches configs/default_config.json in the Python project.
const DEFAULT_CONFIG = JSON.stringify(
  {
    include_confidence: true,
    include_provenance: true,
    on_missing: "null",
  },
  null,
  2
);

const API_URL = "http://localhost:8000/transform";

// ---------------------------------------------------------------------------
// Styles (plain objects — no external CSS lib needed)
// ---------------------------------------------------------------------------
const S = {
  h1:      { fontSize: 22, marginBottom: 4 },
  label:   { display: "block", fontWeight: "bold", marginTop: 16, marginBottom: 4 },
  input:   { display: "block", marginBottom: 8, fontSize: 14 },
  textarea: {
    width: "100%", height: 160, fontFamily: "monospace",
    fontSize: 13, boxSizing: "border-box", padding: 8,
  },
  button: {
    marginTop: 20, padding: "10px 28px", fontSize: 15,
    cursor: "pointer", background: "#0070f3", color: "#fff",
    border: "none", borderRadius: 4,
  },
  buttonDisabled: { opacity: 0.6, cursor: "not-allowed" },
  output: {
    marginTop: 24, background: "#f4f4f4", padding: 16,
    borderRadius: 4, whiteSpace: "pre-wrap", fontSize: 13,
    maxHeight: 600, overflowY: "auto",
  },
  error:  { marginTop: 16, color: "#c0392b", fontWeight: "bold" },
  status: { marginTop: 12, color: "#555", fontSize: 13 },
};

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------
export default function Home() {
  const [csvFile,     setCsvFile]     = useState(null);
  const [resumeFiles, setResumeFiles] = useState([]);
  const [notesFiles,  setNotesFiles]  = useState([]);
  const [configText,  setConfigText]  = useState(DEFAULT_CONFIG);
  const [result,      setResult]      = useState(null);
  const [error,       setError]       = useState(null);
  const [loading,     setLoading]     = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setResult(null);

    // Validate: at least one source file must be provided
    if (!csvFile && resumeFiles.length === 0 && notesFiles.length === 0) {
      setError("Please upload at least a CSV file, a resume, or a notes file.");
      return;
    }

    // Validate config JSON before sending so the user gets an immediate error
    let parsedConfig;
    try {
      parsedConfig = JSON.parse(configText);
    } catch {
      setError("Config is not valid JSON. Please fix it and try again.");
      return;
    }

    // Build multipart form data
    const form = new FormData();
    if (csvFile)          form.append("csv_file", csvFile);
    for (const rf of resumeFiles) form.append("resume_files", rf);
    for (const nf of notesFiles)  form.append("notes_files",  nf);
    form.append("config", JSON.stringify(parsedConfig));

    setLoading(true);
    try {
      const res = await fetch(API_URL, { method: "POST", body: form });
      const data = await res.json();

      if (!res.ok) {
        // FastAPI returns { detail: "..." } for HTTPException
        setError(data.detail ?? `Server error ${res.status}`);
      } else {
        setResult(JSON.stringify(data, null, 2));
      }
    } catch (err) {
      setError(`Could not reach the API at ${API_URL}. Is the backend running?`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main>
      <h1 style={S.h1}>Candidate Data Transformer</h1>
      <p style={{ color: "#555", marginTop: 0 }}>
        Upload a recruiter CSV and/or resume files to produce canonical candidate profiles.
      </p>

      <form onSubmit={handleSubmit}>

        {/* CSV input */}
        <label style={S.label}>CSV file (optional)</label>
        <input
          style={S.input}
          type="file"
          accept=".csv"
          onChange={(e) => setCsvFile(e.target.files[0] ?? null)}
        />

        {/* Resume input — multiple files allowed */}
        <label style={S.label}>Resume files — PDF or DOCX (optional, multiple allowed)</label>
        <input
          style={S.input}
          type="file"
          accept=".pdf,.docx"
          multiple
          onChange={(e) => setResumeFiles(Array.from(e.target.files))}
        />

        {/* Recruiter notes input */}
        <label style={S.label}>Recruiter notes — .txt (optional, multiple allowed)</label>
        <input
          style={S.input}
          type="file"
          accept=".txt"
          multiple
          onChange={(e) => setNotesFiles(Array.from(e.target.files))}
        />

        {/* Config textarea */}
        <label style={S.label}>Runtime config JSON</label>
        <textarea
          style={S.textarea}
          value={configText}
          onChange={(e) => setConfigText(e.target.value)}
          spellCheck={false}
        />

        {/* Submit */}
        <button
          type="submit"
          style={loading ? { ...S.button, ...S.buttonDisabled } : S.button}
          disabled={loading}
        >
          {loading ? "Transforming…" : "Transform"}
        </button>
      </form>

      {/* Loading state */}
      {loading && <p style={S.status}>Running pipeline — this may take a moment…</p>}

      {/* Error display */}
      {error && <p style={S.error}>Error: {error}</p>}

      {/* Output panel */}
      {result && (
        <>
          <p style={{ ...S.status, marginTop: 20, fontWeight: "bold" }}>
            Output ({JSON.parse(result).count} candidate(s))
          </p>
          <pre style={S.output}>{result}</pre>
        </>
      )}
    </main>
  );
}
