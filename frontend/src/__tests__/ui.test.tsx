import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import Login from "../pages/Login";
import Tuto from "../Tuto";

beforeAll(() => {
  // jsdom ne gère pas <dialog> : on simule l'ouverture et la fermeture.
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

describe("Tuto", () => {
  it("explique chaque valeur d'une carte et se referme", async () => {
    render(<Tuto topic="opportunite" />);
    const dialog = document.querySelector("dialog")!;
    expect(dialog.hasAttribute("open")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "? Tuto" }));
    expect(dialog.hasAttribute("open")).toBe(true);
    for (const term of ["Risque (1 à 5 points)", "Stop suggéré", "Évolution passée (médiane)", "Fondamentaux"])
      expect(screen.getByText(term)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Fermer", hidden: true }));
    expect(dialog.hasAttribute("open")).toBe(false);
  });

  it("couvre les écrans simulation, ETF et backtest", () => {
    for (const [topic, term] of [["simulation", "Suivi automatique"], ["etf", "Mode objectif"], ["backtest", "Ratio de Sharpe"]] as const) {
      render(<Tuto topic={topic} />);
      expect(screen.getByText(term)).toBeTruthy();
      cleanup();
    }
  });
});

describe("Connexion", () => {
  it("masque la création de compte quand les inscriptions sont fermées", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, { registration_open: false })));
    render(<Login onAuth={() => undefined} />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Créer un compte" })).toBeNull();
  });

  it("connecte l'utilisateur et affiche une erreur d'identifiants", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json(200, { registration_open: false }))
      .mockResolvedValueOnce(json(401, { detail: "Identifiants invalides" }))
      .mockResolvedValueOnce(json(200, { id: 1, email: "a@b.fr" }));
    vi.stubGlobal("fetch", fetchMock);
    const onAuth = vi.fn();
    render(<Login onAuth={onAuth} />);
    await userEvent.type(screen.getByLabelText(/E-mail/), "a@b.fr");
    await userEvent.type(screen.getByLabelText(/Mot de passe/), "un-mot-de-passe-long");
    await userEvent.click(screen.getByRole("button", { name: "Se connecter" }));
    expect(await screen.findByText("Identifiants invalides")).toBeTruthy();
    expect(onAuth).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Se connecter" }));
    await waitFor(() => expect(onAuth).toHaveBeenCalledWith({ id: 1, email: "a@b.fr" }));
    expect(fetchMock.mock.calls[2][0]).toBe("/api/auth/login");
  });
});
