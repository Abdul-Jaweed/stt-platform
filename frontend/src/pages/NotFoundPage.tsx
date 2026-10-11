import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return (
    <section className="page not-found-page">
      <h2>Not found</h2>
      <p>
        <Link to="/">Back to upload</Link>
      </p>
    </section>
  );
}
