using System;

namespace MiniPlant.Core
{
    public partial class Recipe
    {
        public const int MaxPoints = 1000;

        public bool Validate()
        {
            if (_points.Count == 0)
            {
                throw new InvalidOperationException("Recipe has no points");
            }

            return _points.Count <= MaxPoints;
        }
    }
}
