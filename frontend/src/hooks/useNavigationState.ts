import { useContext } from "react";
import {
  NavigationStateContext,
  type NavigationStateContextValue,
} from "../context/NavigationStateContext";

/**
 * Hook to access the global navigation state (exchange, market, filters).
 * Must be used within a NavigationStateProvider.
 */
export function useNavigationState(): NavigationStateContextValue {
  const ctx = useContext(NavigationStateContext);
  if (ctx === null) {
    throw new Error(
      "useNavigationState must be used within a NavigationStateProvider",
    );
  }
  return ctx;
}
