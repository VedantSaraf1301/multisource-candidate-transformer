export const metadata = {
  title: "Candidate Data Transformer",
  description: "Upload CSV and resumes to produce canonical candidate profiles.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "monospace", maxWidth: 900, margin: "40px auto", padding: "0 20px" }}>
        {children}
      </body>
    </html>
  );
}
