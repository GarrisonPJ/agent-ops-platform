import { configureStore } from "@reduxjs/toolkit";
import { experimentsApi } from "../services/experimentsApi";

export const phase1Store = configureStore({
  reducer: { [experimentsApi.reducerPath]: experimentsApi.reducer },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware().concat(experimentsApi.middleware),
});

export type Phase1RootState = ReturnType<typeof phase1Store.getState>;
export type Phase1AppDispatch = typeof phase1Store.dispatch;
