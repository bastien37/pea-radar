import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, eur, pct, weeks } from "../api";

const reply = (status: number, body: unknown) =>
  vi.fn().mockResolvedValue(new Response(status === 204 ? null : JSON.stringify(body), { status }));

afterEach(() => vi.unstubAllGlobals());

describe("formatage", () => {
  it("affiche les pourcentages avec leur signe", () => {
    expect(pct(2.14)).toBe("+2,1 %");
    expect(pct(-3)).toBe("-3 %");
    expect(pct(0)).toBe("0 %");
    expect(pct(8.413, 2)).toBe("+8,41 %");
  });

  it("convertit les séances en durée lisible", () => {
    expect(weeks(20)).toBe("≈ 4 semaines");
    expect(weeks(90)).toBe("≈ 4 mois");
  });

  it("formate les montants en euros", () => {
    expect(eur(1234.5).replace(/\s/g, " ")).toBe("1 234,50 €");
  });
});

describe("client de l'API", () => {
  it("envoie l'en-tête anti-CSRF et le cookie de session, jamais de jeton", async () => {
    const fetchMock = reply(200, { ok: true });
    vi.stubGlobal("fetch", fetchMock);
    await api.post("/scans", { a: 1 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/scans");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(init.headers["X-Requested-With"]).toBe("XMLHttpRequest");
    expect(init.headers.Authorization).toBeUndefined();
    expect(init.body).toBe('{"a":1}');
  });

  it("remonte le message d'erreur du serveur", async () => {
    vi.stubGlobal("fetch", reply(409, { detail: "Un scan est déjà en cours" }));
    await expect(api.post("/scans")).rejects.toMatchObject({ status: 409, message: "Un scan est déjà en cours" });
  });

  it("assemble les erreurs de validation", async () => {
    vi.stubGlobal("fetch", reply(422, { detail: [{ msg: "trop court" }, { msg: "requis" }] }));
    const err = (await api.post("/auth/register").catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("trop court ; requis");
  });

  it("accepte une réponse sans contenu", async () => {
    vi.stubGlobal("fetch", reply(204, null));
    await expect(api.post("/auth/logout")).resolves.toBeUndefined();
  });
});
