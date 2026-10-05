using System;
using System.Collections.Generic;

namespace MiniPlant.Core
{
    /// <summary>Generic methods: called with two different type arguments.</summary>
    public class Registry
    {
        private readonly Dictionary<Type, object> _items = new Dictionary<Type, object>();

        public void Add<T>(T item) where T : class
        {
            _items[typeof(T)] = item;
        }

        public T Get<T>() where T : class
        {
            return (T)_items[typeof(T)];
        }
    }

    /// <summary>Extension method.</summary>
    public static class StageExtensions
    {
        public static void MoveHome(this IStage stage)
        {
            stage.Move(0.0);
        }
    }
}
