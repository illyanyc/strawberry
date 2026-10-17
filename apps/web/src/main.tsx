import "./styles.css";

import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { setUnauthenticatedHandler } from "@/lib/api";
import { queryClient, router } from "@/router";

setUnauthenticatedHandler(() => {
  queryClient.clear();
  void router.navigate({ to: "/login" });
});

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
