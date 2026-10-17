import { useContext } from "react";
import { SetupStreamContext, type SetupStreamValue } from "./SetupStreamContext";

export function useSetupStream(): SetupStreamValue {
  const ctx = useContext(SetupStreamContext);
  if (!ctx) throw new Error("useSetupStream must be used inside SetupStreamProvider");
  return ctx;
}
