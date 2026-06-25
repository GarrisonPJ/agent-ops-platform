import { configureStore } from "@reduxjs/toolkit";
import { api } from "../services/api";
import trajectoryReducer from "./trajectorySlice";

export const store = configureStore({
  reducer: {
    [api.reducerPath]: api.reducer,
    trajectory: trajectoryReducer,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware().concat(api.middleware),
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
